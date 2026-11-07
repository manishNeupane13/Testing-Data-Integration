import asyncio, json, random, time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlsplit
import httpx, jmespath
from backend.core.settings import CONCURRENCY, MAX_RESPONSE_BYTES
from backend.authentication.provider import AuthenticationProvider
from backend.security.safety import validate_url, pinned_target
class HTTPExecutor:
    def __init__(self,secrets,client=None,url_validator=validate_url):
        self.client=client or httpx.AsyncClient(follow_redirects=False,trust_env=False,limits=httpx.Limits(max_connections=CONCURRENCY))
        self.auth=AuthenticationProvider(secrets);self.secrets=secrets;self.semaphore=asyncio.Semaphore(CONCURRENCY)
        self.validate_url=url_validator;self.locks={};self.last={}
        self.metrics={'requests':0,'retries':0,'bytes':0}
    async def close(self): await self.client.aclose()
    async def request(self,c,e,url,query):
        if not c.enabled: raise ValueError('Connection disabled')
        address=await self.validate_url(url,c.approved_hosts)
        headers={**c.default_headers,**e.headers,'Content-Type':e.content_type}
        query={**dict(httpx.URL(url).params),**query}
        await self.auth.apply(c,self.client,headers,query)
        if headers.get('Authorization'):
            self.secrets.values.add(headers['Authorization'])
            self.secrets.values.add(headers['Authorization'].split(' ',1)[-1])
        target,headers,extensions=pinned_target(url,address,headers)
        attempts=e.retry.attempts if e.method in ('GET','PUT','DELETE') or e.retry.retry_unsafe else 1
        service=urlsplit(c.base_url).netloc
        lock=self.locks.setdefault(service,asyncio.Lock())
        for attempt in range(attempts):
            async with self.semaphore:
                async with lock:
                    await asyncio.sleep(max(0,1/c.requests_per_second-(time.monotonic()-self.last.get(service,0))))
                    self.last[service]=time.monotonic()
                started=datetime.now(timezone.utc).isoformat()
                try:
                    async with self.client.stream(e.method,target,extensions=extensions,params=query,headers=headers,json=e.body if e.content_type=='application/json' else None,content=e.body if e.content_type!='application/json' else None,timeout=e.timeout or c.timeout) as r:
                        self.metrics['requests']+=1
                        chunks=[];size=0
                        async for chunk in r.aiter_bytes():
                            size+=len(chunk)
                            if size>MAX_RESPONSE_BYTES: raise ValueError('Response size limit exceeded')
                            chunks.append(chunk)
                        raw=b''.join(chunks);self.metrics['bytes']+=size
                        if 300<=r.status_code<400: raise ValueError('Redirects disabled; configure the final approved URL')
                        if r.status_code in (429,502,503,504) and attempt+1<attempts:
                            delay=r.headers.get('Retry-After')
                            try: wait=float(delay)
                            except (TypeError,ValueError):
                                try: wait=(parsedate_to_datetime(delay)-datetime.now(timezone.utc)).total_seconds()
                                except Exception: wait=e.retry.backoff*2**attempt+random.random()*.1
                            self.metrics['retries']+=1
                        else:
                            r.raise_for_status()
                            data=json.loads(raw) if e.response_format=='json' and raw else raw.decode(errors='replace')
                            return {'parsed':data,'raw':raw.decode(errors='replace') if e.capture_raw else None,'status':r.status_code,'request_timestamp':started,'response_timestamp':datetime.now(timezone.utc).isoformat(),'headers':dict(r.headers),'bytes':size}
                except (httpx.TransportError,) :
                    if attempt+1>=attempts: raise
                    wait=e.retry.backoff*2**attempt+random.random()*.1;self.metrics['retries']+=1
            if wait>300: raise ValueError('Retry-After exceeds the 300-second retry budget; submit again later')
            await asyncio.sleep(max(wait,0))
    async def execute(self,c,e):
        path=e.path
        for key,value in e.path_params.items(): path=path.replace('{'+key+'}',quote(str(value),safe=''))
        url=urljoin(c.base_url.rstrip('/')+'/',path.lstrip('/')); query=dict(e.query)
        p=e.pagination;items=[];pages=[];seen=set();partial=None
        if p.strategy!='none': query[p.size_parameter]=p.page_size
        if p.strategy in ('page','offset'): query[p.parameter]=p.start if p.strategy=='page' else 0
        for index in range(p.max_pages):
            marker=(url,json.dumps(query,sort_keys=True,default=str))
            if marker in seen: partial={'category':'pagination_loop','message':'Repeated pagination URL or cursor'};break
            seen.add(marker)
            try: response=await self.request(c,e,url,query)
            except Exception as exc:
                if not pages: raise
                partial={'category':type(exc).__name__,'message':str(exc)};break
            pages.append(response)
            data=response['parsed'];chunk=jmespath.search(p.items_path,data) if p.items_path else data
            if p.strategy=='none':
                items=jmespath.search(e.output_path,data) if e.output_path else chunk;break
            if not isinstance(chunk,list): partial={'category':'pagination','message':'Items path must select an array'};break
            items.extend(chunk[:max(0,p.max_records-len(items))])
            if len(items)>=p.max_records or (not chunk and p.strategy in ('offset','page')) or (p.stop_expression and jmespath.search(p.stop_expression,data)): break
            if p.strategy=='cursor':
                nxt=jmespath.search(p.next_path,data)
                if nxt is None or nxt=='': break
                query[p.parameter]=nxt
            elif p.strategy=='offset': query[p.parameter]+=p.page_size
            elif p.strategy=='page': query[p.parameter]+=1
            elif p.strategy=='next_url':
                nxt=jmespath.search(p.next_path,data)
                if not nxt: break
                url=urljoin(url,nxt);query={}
            elif p.strategy=='link':
                links=httpx.Response(200,headers=response['headers']).links;nxt=links.get('next',{}).get('url')
                if not nxt: break
                url=urljoin(url,nxt);query={}
        return self.secrets.redact({'endpoint_id':e.id,'data':items,'pages':pages,'pagination':{'pages':len(pages),'records':len(items) if isinstance(items,list) else None,'partial':partial,'limit_reached':len(pages)==p.max_pages and p.strategy!='none'}})
