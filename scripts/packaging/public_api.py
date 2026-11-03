"""Public HTTPS/WSS edge. Only Caddy is published; API and PostgreSQL stay private."""

from copy import deepcopy
import ipaddress
import re

PRODUCTION_CA = "https://acme-v02.api.letsencrypt.org/directory"
STAGING_CA = "https://acme-staging-v02.api.letsencrypt.org/directory"


def validate(host, email, port, http_port, subnet):
    if (
        not isinstance(host, str)
        or len(host) > 253
        or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host)
        or host.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("ACME requires a lowercase DNS domain, not an IP, wildcard or URL")
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,63}", email or ""):
        raise ValueError("Provide an ACME contact email")
    if any(type(p) is not int or not 1 <= p <= 65535 for p in (port, http_port)) or port == http_port:
        raise ValueError("Choose distinct HTTP and HTTPS ports")
    if set((port, http_port)) & {8765, 8770, 8443, 8874}:
        raise ValueError("Public ports must not replace existing product/LAN ports")
    try:
        network = ipaddress.ip_network(subnet)
    except ValueError:
        raise ValueError("Proxy subnet must be a private IPv4 /28 to /24 network") from None
    private_ranges = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    if (
        network.version != 4
        or not 24 <= network.prefixlen <= 28
        or not any(network.subnet_of(ipaddress.ip_network(value)) for value in private_ranges)
    ):
        raise ValueError("Proxy subnet must be a private IPv4 /28 to /24 network")
    return str(network.broadcast_address - 1)


def caddyfile(host, email, port=443, *, staging=False):
    validate(host, email, port, 80 if port != 80 else 8080, "172.30.80.0/28")
    authority = host if port == 443 else f"{host}:{port}"
    issuer = STAGING_CA if staging else PRODUCTION_CA
    return f"""{{
    email {email}
    acme_ca {issuer}
    admin 127.0.0.1:2019
    persist_config off
}}
http://{host} {{
    redir https://{authority}{{uri}} permanent
}}
https://{host} {{
    request_body {{
        max_size 16KB
    }}
    @api path /api/online/v2/* /healthz /readyz
    handle @api {{
        reverse_proxy api:8784 {{
            header_up Host {authority}
            header_up X-Forwarded-For {{remote_host}}
            header_up X-Forwarded-Proto https
            header_up -Forwarded
        }}
    }}
    handle {{
        respond "API endpoint not found" 404
    }}
}}
"""


def compose(base, caddy_image, host, email, port=443, http_port=80, bind="0.0.0.0", subnet="172.30.80.0/28"):
    proxy_ip = validate(host, email, port, http_port, subnet)
    if bind not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("Invalid public bind")
    document = deepcopy(base)
    services = document["services"]
    api = services["api"]
    command = api["entrypoint"]
    index = command.index("--ssl-certfile")
    del command[index:index + 4]
    index = command.index("--no-proxy-headers")
    command[index:index + 1] = ["--proxy-headers", "--forwarded-allow-ips", proxy_ip]
    api.pop("ports")
    api.pop("volumes")
    api["networks"] = ["proxy", "backend"]
    api["healthcheck"]["test"] = ["CMD", "python", "-c", "import os,urllib.request; u=os.environ['PTCG_ONLINE_V2_PUBLIC_ORIGIN']; r=urllib.request.Request('http://127.0.0.1:8784/readyz',headers={'Host':u.split('://',1)[1]}); urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=4)"]
    services["database"]["networks"] = ["backend"]
    services["migrate"]["networks"] = ["backend"]
    services["caddy"] = {
        "image": caddy_image,
        "platform": api["platform"],
        "pull_policy": "never",
        "restart": "unless-stopped",
        "read_only": True,
        "tmpfs": ["/tmp"],
        "cap_drop": ["ALL"],
        "cap_add": ["NET_BIND_SERVICE"],
        "security_opt": ["no-new-privileges:true"],
        "cpus": 0.5,
        "mem_limit": "192m",
        "ports": [f"{bind}:{http_port}:80", f"{bind}:{port}:443"],
        "volumes": [
            {"type":"bind", "source":"./Caddyfile", "target":"/etc/caddy/Caddyfile", "read_only":True, "bind":{"create_host_path":False}},
            "caddy_data:/data", "caddy_config:/config",
        ],
        "networks": {"edge": {}, "proxy": {"ipv4_address": proxy_ip}},
        "depends_on": {"api": {"condition":"service_healthy"}},
        "healthcheck": {"test":["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:2019/config/"], "interval":"10s", "timeout":"3s", "retries":12},
    }
    document["networks"] = {
        "edge": {},
        "proxy": {"internal":True, "ipam":{"config":[{"subnet":subnet, "ip_range":str(next(ipaddress.ip_network(subnet).subnets(prefixlen_diff=1)))}]}},
        "backend": {"internal":True},
    }
    document["volumes"].update(caddy_data={}, caddy_config={})
    for service in services.values():
        service["logging"] = {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        }
    return document
