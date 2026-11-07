"""Offline tests for imagegen.py ($img): config reread, daily cap race, concurrency,
reload continuity, quota refund rules, Sharey URL validation, input checks.
No network: requests.post is replaced for the duration of the test."""

import base64
import importlib.util
import json as _j
import os
import sys
import tempfile
import threading
import time
import types as T

import pytest
import requests
import urllib3

from conftest import ROOT, _install_shim


def _load():
    spec = importlib.util.spec_from_file_location('imagegen_plugin', ROOT / 'imagegen.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['imagegen_plugin'] = mod
    spec.loader.exec_module(mod)
    return mod


def _reload(_old):
    return _load()


def test_imagegen_offline(monkeypatch):
    _install_shim()
    from ibot.sopel_shim.config import Config
    from ibot.sopel_shim.tools import SopelMemory
    m = _load()
    monkeypatch.setattr(requests, 'post', requests.post)  # restored after the test
    class DB:
        def __init__(s): s.d={}
        def get_plugin_value(s,p,k,default=None):
            v=s.d.get((p,k),default); time.sleep(0.002); return dict(v) if isinstance(v,dict) else v
        def set_plugin_value(s,p,k,v): time.sleep(0.002); s.d[(p,k)]=dict(v)

    cfgfile = tempfile.NamedTemporaryFile('w', suffix='.cfg', delete=False)
    cfgfile.write('[core]\nnick = t\n'); cfgfile.close()

    def mkbot(cap=100, cd=60, conc=2):
        b=T.SimpleNamespace(memory=SopelMemory(), db=DB(), out=[])
        b.config=Config(cfgfile.name)
        with open(cfgfile.name,'w') as f:
            f.write('[core]\nnick = t\n[imagegen]\naccount_id = %s\napi_token = x\ndaily_cap = %d\nuser_cooldown = %d\nmax_concurrent = %d\n' % ('a'*32, cap, cd, conc))
        b.reply=lambda t: b.out.append(t)
        m.setup(b); return b

    class Tr:
        def __init__(s,n,p): s.nick=n; s.sender='#t'; s._p=p
        def group(s,i): return s._p
    import json as _j, urllib3
    class R:
        def __init__(s,c,j=None,raw=None,headers=None):
            s.status_code=c; s.raw_body=raw if raw is not None else _j.dumps(j if j is not None else {}).encode()
            s.headers=headers or {}; s.closed=False; s.read=0
        def __enter__(s): return s
        def __exit__(s,*a): s.closed=True
        def iter_content(s,n):
            for i in range(0,len(s.raw_body),n):
                s.read+=n; yield s.raw_body[i:i+n]
        def json(s): raise AssertionError('must not use eager .json()')
    class Huge(R):
        def __init__(s): super().__init__(200,{}); s.read=0
        def iter_content(s,n):
            while True:
                s.read+=n; yield b'x'*n
    def connfail():
        return requests.ConnectionError(urllib3.exceptions.MaxRetryError(None,'u',urllib3.exceptions.NewConnectionError(None,'refused')))
    IMG=base64.b64encode(b'\xff'*500).decode()
    OKCF=R(200,{'success':True,'result':{'image':IMG}}); OKSH=R(200,{'urls':['https://sharey.org/ABC123']})
    def net(cf, sh, delay=0):
        def post(url, **k):
            assert k.get('timeout') and k.get('stream') is True, k
            if 'cloudflare' in url:
                assert k['json'] == {'prompt': k['json']['prompt'], 'steps': 4}
                time.sleep(delay)
                if isinstance(cf, Exception): raise cf
                return cf
            assert k['data']['expiry']=='30d'
            if isinstance(sh, Exception): raise sh
            return sh
        m.requests.post = post
    cnt=lambda b: b.db.d.get(('imagegen','daily'),{}).get('count',0)

    # 1. config added AFTER start is seen without rehash (Config object built before section existed)
    b=T.SimpleNamespace(memory=SopelMemory(), db=DB(), out=[]); b.reply=lambda t:b.out.append(t)
    open(cfgfile.name,'w').write('[core]\nnick=t\n'); b.config=Config(cfgfile.name); m.setup(b)
    net(OKCF,OKSH); m.img_cmd(b,Tr('a','cat')); assert 'not configured' in b.out[-1]
    time.sleep(0.01); open(cfgfile.name,'a').write('[imagegen]\naccount_id = %s\napi_token = x\n' % ('a'*32))
    m.img_cmd(b,Tr('a','cat')); assert b.out[-1]=='https://sharey.org/ABC123', b.out

    # 2. concurrent cap race: 30 users at once, cap 5, concurrency 50 -> exactly 5 succeed
    b=mkbot(cap=5, conc=50); net(OKCF,OKSH,delay=0.01)
    ts=[threading.Thread(target=m.img_cmd,args=(b,Tr('u%d'%i,'cat'))) for i in range(30)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert b.out.count('https://sharey.org/ABC123')==5 and cnt(b)==5, (b.out.count('https://sharey.org/ABC123'), cnt(b))

    # 3. concurrency guard: max 2 in flight
    b=mkbot(cap=100, conc=2); net(OKCF,OKSH,delay=0.3)
    ts=[threading.Thread(target=m.img_cmd,args=(b,Tr('v%d'%i,'cat'))) for i in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum('busy' in o for o in b.out)==3, b.out

    # 4. reload: new module instance shares counter, cooldown, lock, semaphore
    b=mkbot(cap=2); net(OKCF,OKSH); m.img_cmd(b,Tr('r','cat'))
    lock_before=b.memory['imagegen_lock']
    m=_reload(m); m.setup(b); net(OKCF,OKSH)
    assert b.memory['imagegen_lock'] is lock_before
    m.img_cmd(b,Tr('r','cat')); assert 'slow down' in b.out[-1]
    m.img_cmd(b,Tr('s','cat')); m.img_cmd(b,Tr('t','cat')); assert 'cap' in b.out[-1] and cnt(b)==2

    # 5. refund rules (True = slot given back)
    huge=Huge()
    cases=[
        (R(429),OKSH,'rate limit',True),
        (R(401),OKSH,'auth',True),
        (R(400,{'success':False,'errors':[{'message':'NSFW flagged'}]}),OKSH,'safety',True),
        (R(400,{'success':False,'errors':'oops'}),OKSH,'failed',True),
        (R(503),OKSH,'failed',False),
        (connfail(),OKSH,'unreachable',True),
        (requests.ConnectTimeout(),OKSH,'unreachable',True),
        (requests.ConnectionError('Connection aborted. RemoteDisconnected'),OKSH,'connection failed',False),
        (requests.ReadTimeout(),OKSH,'connection failed',False),
        (R(200,raw=b'[1,2]'),OKSH,'failed',False),
        (R(200,raw=b'not json'),OKSH,'failed',False),
        (R(200,{'success':True,'result':[1]}),OKSH,'no image',False),
        (R(200,{'success':True,'result':'str'}),OKSH,'no image',False),
        (R(200,{'success':True,'result':{'image':5}}),OKSH,'no image',False),
        (R(200,{'success':True,'result':{'image':'!!'}}),OKSH,'no image',False),
        (huge,OKSH,'oversized',False),
        (R(200,{},headers={'Content-Length':str(99*1024*1024)}),OKSH,'oversized',False),
        (OKCF,R(500,{'error':'x'}),'upload',False),
        (OKCF,requests.ReadTimeout(),'upload',False),
        (OKCF,R(200,raw=b'[]'),'upload',False),
        (OKCF,R(200,{'urls':'https://sharey.org/ABC123'}),'upload',False),
        (OKCF,R(200,{'urls':[]}),'upload',False),
        (OKCF,R(200,{'urls':[None]}),'upload',False),
        (OKCF,Huge(),'upload',False),
    ]
    for bad in ['https://sharey.org/ABC123\n','https://sharey.org/ABC123\r','https://sharey.org/ABC123\r\n',
                'https://sharey.org/AB\nC123','https://sharey.org/ABC 123','http://sharey.org/ABC123',
                'https://sharey.org.evil.com/ABC123','https://evil.com/sharey.org/ABC123',
                'https://sharey.org@evil.com/ABC123','https://sharey.org/../ABC123','https://sharey.org/ABC123\x00']:
        cases.append((OKCF,R(200,{'urls':[bad]}),'upload',False))
    cases.append((OKCF,R(200,{'urls':['https://sharey.org/files/ABC123.png']}),'https://sharey.org/files/ABC123.png',False))
    cases.append((OKCF,R(201,{'urls':['https://sharey.org/WTsoLy.jpg']}),'https://sharey.org/WTsoLy.jpg',False))
    cases.append((OKCF,R(202,{'urls':['https://sharey.org/WTsoLy.jpg']}),'upload',False))
    for cf,sh,exp,refund in cases:
        b=mkbot(); net(cf,sh); m.img_cmd(b,Tr('z','cat'))
        assert exp in b.out[-1], (exp, b.out)
        assert cnt(b)==(0 if refund else 1), (exp, cnt(b))
        assert all(not any(c in o for c in '\r\n\x00') for o in b.out)
    assert huge.read <= m.MAX_RESP + 2*65536, huge.read

    # 5b. refund after UTC midnight must not touch the new day's count
    b=mkbot(); real=m._today
    day=['2026-11-07']; m._today=lambda: day[0]
    def post(url, **k):
        day[0]='2026-11-08'
        b.db.d[('imagegen','daily')]={'day':'2026-11-08','count':3}
        return R(429)
    m.requests.post=post; m.img_cmd(b,Tr('mid','cat'))
    assert b.db.d[('imagegen','daily')]=={'day':'2026-11-08','count':3}, b.db.d
    m._today=real

    # 6. input validation
    b=mkbot(); m.img_cmd(b,Tr('d','')); assert 'usage' in b.out[-1]
    m.img_cmd(b,Tr('d','x'*500)); assert 'too long' in b.out[-1]; assert cnt(b)==0
    os.unlink(cfgfile.name)

