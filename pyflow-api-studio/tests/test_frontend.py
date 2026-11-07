"""Render each management page against a deterministic API stub."""
import json
import httpx
import pytest
from pathlib import Path
from streamlit.testing.v1 import AppTest

@pytest.mark.parametrize('page',['Data Workbench','Custom Python','Connections','Endpoint Registry','Endpoint Blueprint Editor','HTTP Request Tester','JSON Response Explorer','JSON Transformation Builder','Workflow Builder','Workflow Execution','Execution History','Stored Results','Settings'])
def test_management_pages(monkeypatch,page):
    spec=json.loads(Path('examples/blueprints.json').read_text())
    def request(method,url,**kwargs):
        path=httpx.URL(url).path
        if path=='/python/status': value={'ready':False,'setup':'docker pull python:3.12-slim'}
        elif path=='/json/inspect': value=[{'path':'@','preview':'[]'}]
        elif path in ('/connections','/endpoints','/workflows'): value=spec[path[1:]]
        elif path in ('/jobs','/results','/schedules','/recipes'): value=[]
        elif path=='/settings': value={'metrics':{}}
        else: value={}
        return httpx.Response(200,json=value,request=httpx.Request(method,url))
    monkeypatch.setattr(httpx,'request',request)
    app=AppTest.from_file(str(Path('frontend/app.py').resolve()),default_timeout=15).run()
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception
