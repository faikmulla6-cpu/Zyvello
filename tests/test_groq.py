import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

import harvey.groq as groq
from harvey.brain import Brain
from harvey.config import BrainConfig
from harvey.state import StateManager

KEY = "gsk_" + "a" * 32
KEY2 = "gsk_" + "b" * 32


@pytest.fixture(autouse=True)
def isolate_groq_settings(tmp_path, monkeypatch):
    # Dashboard saves update os.environ too. Restore that state between tests.
    monkeypatch.setattr(groq, 'PROJECT_ROOT', tmp_path)
    for name in (*groq.KEY_NAMES, 'GROQ_ACTIVE_KEY', 'GROQ_AUTO_FALLBACK', 'GROQ_FALLBACK_KEYS'):
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)


@pytest.fixture
def transport(monkeypatch):
    original = httpx.AsyncClient
    calls = []
    responses = []
    def handler(request):
        calls.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(groq.httpx, "AsyncClient", lambda **kw: original(
        **kw, transport=httpx.MockTransport(handler)))
    return calls, responses


def success(text="OK"):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": text}}],
                                     "usage": {"prompt_tokens": 20, "completion_tokens": 4}})


@pytest.mark.asyncio
async def test_selected_key_only_and_rate_limit_pause(tmp_path, monkeypatch, transport):
    (tmp_path / ".env").write_text(f"GROQ_API_KEY={KEY}\nGROQ_API_KEY_2={KEY2}\nGROQ_ACTIVE_KEY=GROQ_API_KEY_2\nGROQ_AUTO_FALLBACK=false\n")
    monkeypatch.setattr(groq, "PROJECT_ROOT", tmp_path)
    calls, responses = transport
    responses.append(httpx.Response(429, headers={"retry-after": "300"}, json={"error": KEY2}))
    client = groq.GroqClient()
    with pytest.raises(groq.GroqError, match="usage limit"):
        await client.run("hello")
    with pytest.raises(groq.GroqError, match="usage limit"):
        await client.run("hello again")
    assert len(calls) == 1
    assert calls[0].headers['authorization'] == f"Bearer {KEY2}"
    assert KEY2 not in client.reason
    client.until = time.monotonic() - 1
    client._cooldowns[KEY2] = time.monotonic() - 1
    responses.append(success())
    assert (await client.run("hello"))[0] == "OK"


@pytest.mark.asyncio
async def test_brain_records_real_tokens_and_separate_budget(tmp_path, monkeypatch, transport):
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    monkeypatch.setattr(groq, "PROJECT_ROOT", tmp_path)
    state = StateManager(str(tmp_path / "state.db"))
    await state.init_db()
    brain = Brain(state, BrainConfig(provider="groq"))
    assert await brain.preflight() == ""
    transport[1].append(success('{"ok":true}'))
    assert await brain.think_json("classify", agent="handler") == {"ok": True}
    assert await state.get_usage_today(provider="groq") == 1
    assert await state.get_usage_today(provider="claude") == 0
    assert not await brain.is_within_budget(max_daily_calls=1)
    async with state._connect() as db:
        row = await (await db.execute("SELECT provider,input_tokens,output_tokens FROM usage_events")).fetchone()
        assert tuple(row) == ("groq", 20, 4)
    transport[1].append(httpx.Response(401, text=KEY))
    assert await brain.think("hello") == ""
    assert "rejected" in brain.paused_reason
    assert KEY not in brain.paused_reason


@pytest.mark.asyncio
async def test_no_key_no_request_and_incomplete_response(transport, monkeypatch, tmp_path):
    monkeypatch.setattr(groq, "PROJECT_ROOT", tmp_path)
    for name in groq.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    client = groq.GroqClient()
    assert "Add and select" in client.preflight()
    with pytest.raises(groq.GroqError):
        await client.run("test")
    assert not transport[0]
    response = success()
    payload = response.json(); payload["choices"][0]["finish_reason"] = "length"
    transport[1].append(httpx.Response(200, json=payload))
    with pytest.raises(groq.GroqError, match="incomplete"):
        await groq.GroqClient(key=KEY).run("test")


def test_settings_preserve_configuration_and_mask_keys(tmp_path, monkeypatch):
    import harvey.dashboard as dash
    import yaml
    env = tmp_path / ".env"
    env.write_text("SMTP_PASSWORD=keep-secret\n")
    config = tmp_path / "harvey.yaml"
    config.write_text("product:\n  name: Keep me\nbrain:\n  reasoning_effort: high\n")
    monkeypatch.setattr(dash, "ENV_FILE", env)
    monkeypatch.setattr(dash, "_resolve_config_file", lambda: config)
    dash.token_store.configure(tmp_path / "dashboard-token")
    client = TestClient(dash.app, base_url="http://127.0.0.1:5555")
    headers = {"Authorization": "Bearer " + dash.token_store.get()}
    payload = {"provider": "groq", "keys": {"GROQ_API_KEY": KEY, "GROQ_API_KEY_2": KEY2},
               "active_key": "GROQ_API_KEY_2", "auto_fallback": True,
               "fallback_keys": ['GROQ_API_KEY']}
    assert client.post('/api/settings/brain', json=payload, headers=headers).json()['success']
    saved = yaml.safe_load(config.read_text())
    assert saved['product']['name'] == 'Keep me'
    assert saved['brain']['reasoning_effort'] == 'high'
    assert 'SMTP_PASSWORD=keep-secret' in env.read_text()
    settings = client.get('/api/settings/brain', headers=headers)
    assert KEY not in settings.text and KEY2 not in settings.text
    assert settings.json()['active_key'] == 'GROQ_API_KEY_2'
    assert settings.json()['auto_fallback'] is True
    assert settings.json()['fallback_keys'] == ['GROQ_API_KEY']
    payload['keys'] = {}  # Empty fields preserve saved keys.
    payload['auto_fallback'] = False
    assert client.post('/api/settings/brain', json=payload, headers=headers).json()['success']
    assert client.get('/api/settings/brain', headers=headers).json()['auto_fallback'] is False
    payload['keys'] = {'GROQ_API_KEY_2': ''}
    assert client.post('/api/settings/brain', json=payload, headers=headers).status_code == 400
    payload['active_key'] = 'GROQ_API_KEY'
    assert client.post('/api/settings/brain', json=payload, headers=headers).json()['success']
    assert not groq.credentials(env)[1]['GROQ_API_KEY_2']
    before = env.read_text()
    payload['keys'] = {'SMTP_PASSWORD': 'overwrite'}
    assert client.post('/api/settings/brain', json=payload, headers=headers).status_code == 400
    assert env.read_text() == before
    payload['keys'] = {}
    payload['fallback_keys'] = ['SMTP_PASSWORD']
    assert client.post('/api/settings/brain', json=payload, headers=headers).status_code == 400
    assert env.read_text() == before


def test_connection_check_uses_selected_key_without_saving(tmp_path, monkeypatch):
    import harvey.dashboard as dash
    monkeypatch.setattr(dash, 'ENV_FILE', tmp_path / '.env')
    dash.token_store.configure(tmp_path / 'dashboard-token')
    run = AsyncMock(return_value=('OK', {}, 1))
    monkeypatch.setattr(groq.GroqClient, 'run', run)
    client = TestClient(dash.app, base_url='http://127.0.0.1:5555')
    headers = {'Authorization': 'Bearer ' + dash.token_store.get()}
    assert client.post('/api/settings/test-groq', json={'key': KEY}, headers=headers).json()['success']
    assert not (tmp_path / '.env').exists()
    assert client.post('/api/settings/test-groq', json={'key': KEY + '\nSMTP_PASSWORD=bad'}, headers=headers).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 403, 429, 500, 503])
async def test_automatic_fallback_and_key_cooldown(status, tmp_path, monkeypatch, transport):
    monkeypatch.setattr(groq, 'PROJECT_ROOT', tmp_path)
    (tmp_path / '.env').write_text(f'GROQ_API_KEY={KEY}\nGROQ_API_KEY_2={KEY2}\nGROQ_AUTO_FALLBACK=true\n')
    calls, responses = transport
    responses.extend([httpx.Response(status), success('backup')])
    client = groq.GroqClient()
    assert (await client.run('same prompt'))[0] == 'backup'
    assert client.last_slot == 'GROQ_API_KEY_2'
    assert [r.headers['authorization'] for r in calls] == ['Bearer '+KEY, 'Bearer '+KEY2]
    assert all(__import__('json').loads(r.content)['messages'][0]['content'] == 'same prompt' for r in calls)
    responses.append(success('backup again'))
    await client.run('next prompt')
    assert len(calls) == 3 and calls[-1].headers['authorization'] == 'Bearer '+KEY2


@pytest.mark.asyncio
@pytest.mark.parametrize('extra', ['GROQ_AUTO_FALLBACK=false', 'GROQ_FALLBACK_KEYS=', 'GROQ_API_KEY_2='+KEY])
async def test_disabled_excluded_and_duplicate_backups(extra, tmp_path, monkeypatch, transport):
    monkeypatch.setattr(groq, 'PROJECT_ROOT', tmp_path)
    (tmp_path / '.env').write_text(f'GROQ_API_KEY={KEY}\nGROQ_API_KEY_2={KEY2}\n{extra}\n')
    transport[1].append(httpx.Response(503))
    with pytest.raises(groq.GroqError):
        await groq.GroqClient().run('test')
    assert len(transport[0]) == 1


@pytest.mark.asyncio
async def test_quota_on_backup_tries_remaining_keys(tmp_path, monkeypatch, transport):
    monkeypatch.setattr(groq, 'PROJECT_ROOT', tmp_path)
    (tmp_path / '.env').write_text(f'GROQ_API_KEY={KEY}\nGROQ_API_KEY_2={KEY2}\nGROQ_API_KEY_3=gsk_'+ 'c'*32+'\n')
    transport[1].extend([httpx.Response(503), httpx.Response(429, headers={'retry-after': '300'}), success('third key')])
    client = groq.GroqClient()
    assert (await client.run('test'))[0] == 'third key'
    assert client.last_slot == 'GROQ_API_KEY_3'
    assert client._cooldowns[KEY2] > time.monotonic() + 290
    transport[1].append(success('third again'))
    assert (await client.run('test again'))[0] == 'third again'
    assert len(transport[0]) == 4


@pytest.mark.asyncio
async def test_all_rate_limited_wait_until_earliest_retry(tmp_path, transport):
    (tmp_path / '.env').write_text(f'GROQ_API_KEY={KEY}\nGROQ_API_KEY_2={KEY2}\n')
    transport[1].extend([httpx.Response(429, headers={'retry-after': '300'}),
                         httpx.Response(429, headers={'retry-after': '120'})])
    client = groq.GroqClient()
    with pytest.raises(groq.GroqError, match='usage limit'):
        await client.run('test')
    assert time.monotonic() + 110 < client.until < time.monotonic() + 125
    with pytest.raises(groq.GroqError):
        await client.run('still waiting')
    assert len(transport[0]) == 2
    client.until = time.monotonic() - 1
    client._cooldowns[KEY2] = time.monotonic() - 1
    transport[1].append(success('recovered'))
    assert (await client.run('retry'))[0] == 'recovered'
    assert transport[0][-1].headers['authorization'] == 'Bearer '+KEY2


@pytest.mark.asyncio
async def test_network_fallback_and_primary_recovery(tmp_path, transport):
    (tmp_path / '.env').write_text(f'GROQ_API_KEY={KEY}\nGROQ_API_KEY_2={KEY2}\n')
    transport[1].extend([httpx.ConnectError('network unavailable'), success('backup')])
    client = groq.GroqClient()
    assert (await client.run('test'))[0] == 'backup'
    client._cooldowns[KEY] = time.monotonic() - 1
    transport[1].append(success('primary'))
    assert (await client.run('test'))[0] == 'primary'
    assert client.last_slot == 'GROQ_API_KEY'


@pytest.mark.asyncio
async def test_model_error_is_not_retried_across_keys(tmp_path, transport):
    (tmp_path / '.env').write_text(f'GROQ_API_KEY={KEY}\nGROQ_API_KEY_2={KEY2}\n')
    transport[1].append(httpx.Response(400))
    with pytest.raises(groq.GroqError, match='model'):
        await groq.GroqClient().run('test')
    assert len(transport[0]) == 1
