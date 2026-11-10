import{c as f,e as O}from"./utils-BglyRxo0.js";/**
 * @license lucide-react v0.468.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const C=f("EyeOff",[["path",{d:"M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49",key:"ct8e1f"}],["path",{d:"M14.084 14.158a3 3 0 0 1-4.242-4.242",key:"151rxh"}],["path",{d:"M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143",key:"13bj9a"}],["path",{d:"m2 2 20 20",key:"1ooewy"}]]);/**
 * @license lucide-react v0.468.0 - ISC
 *
 * This source code is licensed under the ISC license.
 * See the LICENSE file in the root directory of this source tree.
 */const E=f("Eye",[["path",{d:"M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0",key:"1nclc0"}],["circle",{cx:"12",cy:"12",r:"3",key:"1v7zrd"}]]),y=t=>typeof t=="boolean"?`${t}`:t===0?"0":t,m=O,b=(t,n)=>e=>{var c;if((n==null?void 0:n.variants)==null)return m(t,e==null?void 0:e.class,e==null?void 0:e.className);const{variants:r,defaultVariants:i}=n,k=Object.keys(r).map(a=>{const l=e==null?void 0:e[a],s=i==null?void 0:i[a];if(l===null)return null;const d=y(l)||y(s);return r[a][d]}),u=e&&Object.entries(e).reduce((a,l)=>{let[s,d]=l;return d===void 0||(a[s]=d),a},{}),h=n==null||(c=n.compoundVariants)===null||c===void 0?void 0:c.reduce((a,l)=>{let{class:s,className:d,...V}=l;return Object.entries(V).every(N=>{let[v,o]=N;return Array.isArray(o)?o.includes({...i,...u}[v]):{...i,...u}[v]===o})?[...a,s,d]:a},[]);return m(t,k,h,e==null?void 0:e.class,e==null?void 0:e.className)};export{C as E,E as a,b as c};
