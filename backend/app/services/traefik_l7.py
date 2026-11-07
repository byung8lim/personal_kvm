"""File-provider Traefik HTTP routing configuration with explicit allowed features."""
import re
from pathlib import Path
import yaml

HOST = re.compile(r"^(?=.{1,253}$)[a-zA-Z0-9]+(?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?$")
NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")

def render_route(spec):
    if not isinstance(spec,dict): raise ValueError("Route must be a mapping")
    name=spec.get("name")
    domain=spec.get("domain")
    prefix=spec.get("path_prefix","/")
    if not isinstance(name,str) or not NAME.fullmatch(name): raise ValueError("Invalid name")
    if not isinstance(domain,str) or not HOST.fullmatch(domain) or ".." in domain: raise ValueError("Invalid domain")
    if not isinstance(prefix,str) or not prefix.startswith("/") or any(ch in prefix for ch in ("\n","\r","`")):
        raise ValueError("Invalid path prefix")
    targets=spec.get("backends")
    if not isinstance(targets,list) or not 1<=len(targets)<=32:raise ValueError("1..32 backends required")
    servers=[]
    for target in targets:
        if not isinstance(target,dict): raise ValueError("Backend must be an object")
        url=target.get("url")
        if not isinstance(url,str) or not re.fullmatch(r"https?://[a-zA-Z0-9.-]+:[0-9]{1,5}",url) or ".." in url:
            raise ValueError("Invalid backend HTTP URL")
        port=int(url.rsplit(":",1)[1])
        if not 1<=port<=65535: raise ValueError("Invalid backend port")
        servers.append({"url":url})
    tls=spec.get("tls",False)
    if type(tls) is not bool: raise ValueError("tls must be bool")
    rule=f"Host(`{domain}`) && PathPrefix(`{prefix}`)"
    return {"http":{"routers":{name:{"rule":rule,"entryPoints":["websecure" if tls else "web"],
                                "service":name,**({"tls":{}} if tls else {})}},
                    "services":{name:{"loadBalancer":{"servers":servers,"passHostHeader":True}}}}}

def render_yaml(spec):
    return yaml.safe_dump(render_route(spec),sort_keys=False,allow_unicode=True)
