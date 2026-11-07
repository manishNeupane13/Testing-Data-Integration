import asyncio, time, httpx
from backend.security.safety import validate_url, pinned_target
from backend.core.settings import MAX_RESPONSE_BYTES
class AuthenticationProvider:
    def __init__(self,secrets): self.secrets=secrets;self.cache={};self.refresh={};self.lock=asyncio.Lock()
    async def apply(self,connection,client,headers,query):
        c=self.secrets.get(connection.credential_ref); kind=connection.auth_type
        if kind=='api_key_header': headers[connection.auth_name]=c['api_key']
        elif kind=='api_key_query': query[connection.auth_name]=c['api_key']
        elif kind=='bearer': headers['Authorization']='Bearer '+c['token']
        elif kind=='basic':
            import base64
            headers['Authorization']='Basic '+base64.b64encode((c['username']+':'+c['password']).encode()).decode()
        elif kind.startswith('oauth2'):
            async with self.lock:
                cached=self.cache.get(connection.id)
                if not cached or cached[1]<=time.monotonic():
                    address=await validate_url(c['token_url'],connection.approved_hosts)
                    target,token_headers,extensions=pinned_target(c['token_url'],address,{})
                    grant={'grant_type':'client_credentials'} if kind=='oauth2_client_credentials' else {'grant_type':'refresh_token','refresh_token':self.refresh.get(connection.id,c['refresh_token'])}
                    async with client.stream('POST',target,headers=token_headers,extensions=extensions,data={**grant,'client_id':c['client_id'],'client_secret':c['client_secret']},timeout=connection.timeout) as response:
                        response.raise_for_status()
                        if 300<=response.status_code<400: raise ValueError('OAuth redirects disabled')
                        raw=bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw)>MAX_RESPONSE_BYTES: raise ValueError('OAuth response size limit exceeded')
                        import json
                        token=json.loads(raw)
                    self.secrets.values.add(token['access_token'])
                    if token.get('refresh_token'): self.refresh[connection.id]=token['refresh_token'];self.secrets.values.add(token['refresh_token'])
                    self.cache[connection.id]=(token['access_token'],time.monotonic()+max(0,token.get('expires_in',300)-30))
                headers['Authorization']='Bearer '+self.cache[connection.id][0]
