import asyncio, ipaddress, socket, re
from urllib.parse import urlsplit
SENSITIVE = re.compile(r'authorization|cookie|secret|password|token|api.?key',re.I)
class SecurityError(ValueError): pass
class SecretStore:
    def __init__(self): self.values=set()
    def get(self, ref):
        import os, json
        if not ref: return {}
        value=os.environ.get(ref)
        if value is None: raise SecurityError(f'Missing credential environment reference: {ref}')
        data=json.loads(value)
        for v in data.values():
            if isinstance(v,str) and v:
                from urllib.parse import quote, quote_plus
                self.values.update((v,quote(v,safe=''),quote_plus(v)))
        return data
    def redact(self, value):
        if isinstance(value,dict): return {k: '[REDACTED]' if SENSITIVE.search(k) else self.redact(v) for k,v in value.items()}
        if isinstance(value,list): return [self.redact(v) for v in value]
        if isinstance(value,str):
            for secret in sorted(self.values,key=len,reverse=True): value=value.replace(secret,'[REDACTED]')
        return value
async def validate_url(url, approved_hosts):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password: raise SecurityError('Invalid URL')
    if p.hostname not in approved_hosts: raise SecurityError('Host is not approved')
    addresses=await asyncio.get_running_loop().getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses): raise SecurityError('Private or reserved address blocked')
    return addresses[0][4][0]

def validate_config_secrets(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if SENSITIVE.search(key) and key not in ('credential_ref','auth_type','auth_name') and item: raise SecurityError('Store credentials in environment references, not configuration')
            validate_config_secrets(item)
    elif isinstance(value,list):
        for item in value: validate_config_secrets(item)

def pinned_target(url,address,headers):
    """Connect to the validated IP while verifying TLS against the original host."""
    import httpx
    original=httpx.URL(url)
    if address is None: return original,headers,{}  # injected mock validator only
    headers={**headers,'Host':original.netloc.decode('ascii')}
    return original.copy_with(host=address),headers,{'sni_hostname':original.host}
