"""Test-only HTTPX transport: no network, invented vendor endpoints, or private-host exemption."""
import httpx
DRIVERS=[{'id':1,'active':True,'profile':{'name':'Ada'}},{'id':2,'active':False,'profile':{'name':'Lin'}},{'id':3,'active':True,'profile':{'name':'Sam'}}]
def handler(request):
    if request.url.path=='/drivers':
        offset=int(request.url.params.get('offset',0));limit=int(request.url.params.get('limit',100))
        return httpx.Response(200,json={'items':DRIVERS[offset:offset+limit]})
    if request.url.path.startswith('/drivers/'): return httpx.Response(200,json=next(d for d in DRIVERS if d['id']==int(request.url.path.split('/')[-1])))
    if request.url.path=='/teams': return httpx.Response(200,json={'items':[{'id':4,'active':True,'profile':{'name':'Ops'}}]})
    return httpx.Response(404,json={'error':'Unknown mock route'})
