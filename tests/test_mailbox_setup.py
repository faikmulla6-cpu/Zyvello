import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

import harvey.dashboard as dash
import harvey.mailbox_setup as mail

ORIGIN = 'http://127.0.0.1:5555'


@pytest.fixture
def mailbox(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, 'ENV_FILE', tmp_path / '.env')
    config = tmp_path / 'harvey.yaml'
    config.write_text('persona:\n  name: Keep me\n  email: old@example.com\nchannels:\n  email:\n    provider: instantly\n    require_approval: true\n    max_daily_sends: 17\nproduct:\n  name: Keep product\n')
    monkeypatch.setattr(dash, '_resolve_config_file', lambda: config)
    dash.token_store.configure(tmp_path / 'data' / 'dashboard-token')
    mail.PENDING.clear()
    # Settings persistence updates process env too; restore it after each test.
    for keys in mail.KEYS.values():
        for key in keys:
            monkeypatch.setenv(key, '')
            monkeypatch.delenv(key)
    client = TestClient(dash.app, base_url=ORIGIN)
    client.headers['Authorization'] = 'Bearer ' + dash.token_store.get()
    yield client, tmp_path, config
    mail.PENDING.clear()


def save_smtp(client):
    return client.post('/api/mailboxes/smtp/save', json={'fields': {
        'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '465', 'SMTP_USERNAME': 'owner@example.com',
        'SMTP_PASSWORD': 'private-password', 'IMAP_HOST': 'imap.example.com', 'IMAP_PORT': '993'}})


def test_secret_storage_real_status_and_activation_preserve_settings(mailbox, monkeypatch):
    client, path, config = mailbox
    assert client.post('/api/mailboxes/smtp/activate', json={}).status_code == 409
    assert save_smtp(client).json()['success']
    snapshot = client.get('/api/mailboxes').json()
    assert 'private-password' not in json.dumps(snapshot)
    assert snapshot['saved_secrets']['SMTP_PASSWORD']
    assert next(p for p in snapshot['providers'] if p['id']=='smtp')['status']=='saved'
    probe = AsyncMock(return_value=(True, 'owner@example.com'))
    monkeypatch.setattr(mail, 'probe', probe)
    assert client.post('/api/mailboxes/smtp/test', json={}).json()['success']
    assert client.post('/api/mailboxes/smtp/activate', json={}).json()['success']
    saved = yaml.safe_load(config.read_text())
    assert saved['channels']['email']=={'provider':'smtp','require_approval':True,'max_daily_sends':17}
    assert saved['persona']=={'name':'Keep me','email':'owner@example.com'}
    assert saved['product']['name']=='Keep product'
    client.post('/api/mailboxes/smtp/save', json={'fields':{'SMTP_PASSWORD':'changed'}})
    assert client.post('/api/mailboxes/smtp/activate',json={}).status_code==409


@pytest.mark.parametrize('fields', [{'SMTP_PORT':'99999'}, {'SMTP_PORT':'bad'}, {'SMTP_HOST':'https://bad/path'},
                                   {'SMTP_PASSWORD':'x\nINSTANTLY_API_KEY=bad'}, {'GMAIL_CLIENT_SECRET':'bad'}])
def test_invalid_settings_do_not_write(mailbox, fields):
    client, path, _ = mailbox
    assert client.post('/api/mailboxes/smtp/save',json={'fields':fields}).status_code==400
    assert not (path/'.env').exists()


def test_probe_failure_does_not_leak_credentials_or_send(mailbox,monkeypatch):
    client, path, _ = mailbox
    save_smtp(client)
    monkeypatch.setattr(mail,'probe',AsyncMock(side_effect=RuntimeError('private-password')))
    result=client.post('/api/mailboxes/smtp/test',json={})
    assert result.json()['success'] is False
    assert 'private-password' not in result.text
    assert client.post('/api/mailboxes/smtp/send-test',json={'confirm':True}).status_code==409
    assert next(p for p in client.get('/api/mailboxes').json()['providers'] if p['id']=='smtp')['status']=='reconnect'


def test_test_email_requires_explicit_confirmation_and_verified_own_mailbox(mailbox,monkeypatch):
    client, _, _=mailbox
    save_smtp(client)
    monkeypatch.setattr(mail,'probe',AsyncMock(return_value=(True,'owner@example.com')))
    sender=SimpleNamespace(config=SimpleNamespace(persona=SimpleNamespace(email='')),send_email=AsyncMock(return_value=SimpleNamespace(ok=True)))
    monkeypatch.setattr(mail,'adapter',lambda _:sender)
    client.post('/api/mailboxes/smtp/test',json={})
    sender.send_email.assert_not_called()
    assert client.post('/api/mailboxes/smtp/send-test',json={}).status_code==400
    assert client.post('/api/mailboxes/smtp/send-test',json={'confirm':True,'recipient':'someone@example.com'}).json()['success']
    assert sender.send_email.call_args.args[0]=='owner@example.com'


@pytest.mark.parametrize('provider',['gmail','microsoft'])
def test_oauth_state_browser_binding_replay_and_token_storage(mailbox,monkeypatch,provider):
    client,path,_=mailbox
    fields={'GMAIL_CLIENT_ID':'google-id','GMAIL_CLIENT_SECRET':'secret'} if provider=='gmail' else {'M365_CLIENT_ID':'microsoft-id','M365_TENANT_ID':'common'}
    client.post(f'/api/mailboxes/{provider}/save',json={'fields':fields})
    started=client.post(f'/api/mailboxes/oauth/{provider}/start',json={})
    assert started.status_code==200
    cookie=started.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=lax' in cookie
    query=parse_qs(urlparse(started.json()['url']).query)
    assert query['code_challenge_method']==['S256']
    state=query['state'][0]
    callback=f'/api/mailboxes/oauth/{provider}/callback?state={state}&code=code'
    exchange=AsyncMock(return_value={'refresh_token':'secret-refresh','obtained_at':time.time_ns()})
    monkeypatch.setattr(mail,'exchange',exchange)
    stranger=TestClient(dash.app,base_url=ORIGIN)
    assert stranger.get(callback).status_code==400
    response=client.get(callback,follow_redirects=False,headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'})
    assert response.status_code==200 and 'result=authorized' in response.text
    assert 'secret-refresh' not in response.text
    assert json.loads(mail.token_path(provider).read_text())['refresh_token']=='secret-refresh'
    assert client.get(callback).status_code==400
    exchange.assert_awaited_once()


def test_oauth_cancel_expiry_and_changed_settings(mailbox,monkeypatch):
    client,_,_=mailbox
    client.post('/api/mailboxes/gmail/save',json={'fields':{'GMAIL_CLIENT_ID':'id','GMAIL_CLIENT_SECRET':'secret'}})
    def start():
        return parse_qs(urlparse(client.post('/api/mailboxes/oauth/gmail/start',json={}).json()['url']).query)['state'][0]
    state=start()
    result=client.get(f'/api/mailboxes/oauth/gmail/callback?state={state}&error=access_denied',follow_redirects=False)
    assert 'result=cancelled' in result.text
    state=start();mail.PENDING[state]['expires']=time.time()-1
    assert client.get(f'/api/mailboxes/oauth/gmail/callback?state={state}&code=code').status_code==400
    state=start()
    client.post('/api/mailboxes/gmail/save',json={'fields':{'GMAIL_CLIENT_SECRET':'different'}})
    exchange=AsyncMock();monkeypatch.setattr(mail,'exchange',exchange)
    result=client.get(f'/api/mailboxes/oauth/gmail/callback?state={state}&code=code',follow_redirects=False)
    assert 'result=failed' in result.text
    exchange.assert_not_called()


def test_missing_app_credentials_and_owner_protection(mailbox):
    client,_,_=mailbox
    assert client.post('/api/mailboxes/oauth/gmail/start',json={}).status_code==409
    anonymous=TestClient(dash.app,base_url=ORIGIN)
    assert anonymous.get('/api/mailboxes').status_code==401
    assert anonymous.post('/api/mailboxes/smtp/save',json={'fields':{}}).status_code==401
    client.post('/api/auth/signup',json={'email':'owner@example.com','password':'TestPassword123','confirm_password':'TestPassword123'})
    assert anonymous.get('/mailboxes',follow_redirects=False).headers['location']=='/login'


def test_mailbox_controls_live_in_existing_settings_and_old_url_redirects(mailbox):
    client,_,_=mailbox
    response=client.get('/mailboxes',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/#settings'
    page=client.get('/').text
    assert 'id="mailbox-connections-root"' in page
    assert '/static/landing/mailboxes.js' in page
    assert 'Open connection studio' not in page
    for field in ('gmail-id','gmail-secret','smtp-host','smtp-pass','imap-host','imap-pass','m365-client','m365-tenant','instantly-key','reoon-key','zerobounce-key','hunter-key','linkedin-email'):
        assert f'id="{field}"' in page


def test_logging_out_invalidates_pending_mailbox_authorization(mailbox, monkeypatch):
    client,_,_=mailbox
    client.post('/api/auth/signup',json={'email':'owner@example.com','password':'TestPassword123','confirm_password':'TestPassword123'})
    client.post('/api/mailboxes/gmail/save',json={'fields':{'GMAIL_CLIENT_ID':'id','GMAIL_CLIENT_SECRET':'secret'}})
    state=parse_qs(urlparse(client.post('/api/mailboxes/oauth/gmail/start',json={}).json()['url']).query)['state'][0]
    client.post('/api/auth/logout',json={})
    exchange=AsyncMock();monkeypatch.setattr(mail,'exchange',exchange)
    response=client.get(f'/api/mailboxes/oauth/gmail/callback?state={state}&code=code',follow_redirects=False)
    assert response.status_code==200 and 'result=failed' in response.text
    exchange.assert_not_called()


def test_old_connection_check_requires_reverification(mailbox,monkeypatch):
    client,path,_=mailbox
    save_smtp(client)
    monkeypatch.setattr(mail,'probe',AsyncMock(return_value=(True,'owner@example.com')))
    client.post('/api/mailboxes/smtp/test',json={})
    stored=mail.health();stored['smtp']['checked_at']=time.time()-86500
    mail.private_json(path/'data'/'mailbox-health.json',stored)
    assert next(p for p in client.get('/api/mailboxes').json()['providers'] if p['id']=='smtp')['status']=='saved'
    assert client.post('/api/mailboxes/smtp/activate',json={}).status_code==409
    assert client.post('/api/mailboxes/smtp/send-test',json={'confirm':True}).status_code==409


@pytest.mark.asyncio
async def test_exchange_checks_permissions_and_never_exposes_tokens(mailbox,monkeypatch):
    client,_,_=mailbox
    client.post('/api/mailboxes/gmail/save',json={'fields':{'GMAIL_CLIENT_ID':'id','GMAIL_CLIENT_SECRET':'secret'}})
    real=httpx.AsyncClient
    replies=[{'refresh_token':'refresh','scope':'profile'}, {'refresh_token':'refresh','scope':'https://www.googleapis.com/auth/gmail.modify'}]
    def handler(request):
        assert request.url.host=='oauth2.googleapis.com'
        assert b'code_verifier=verifier' in request.content
        return httpx.Response(200,json=replies.pop(0))
    monkeypatch.setattr(mail.httpx,'AsyncClient',lambda **kwargs:real(**kwargs,transport=httpx.MockTransport(handler)))
    pending={'uri':ORIGIN+'/callback','verifier':'verifier'}
    with pytest.raises(ValueError,match='permissions'):
        await mail.exchange('gmail','code',pending)
    assert (await mail.exchange('gmail','code',pending))['refresh_token']=='refresh'
