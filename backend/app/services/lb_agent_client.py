"""Client for the privileged local LB agent; never invokes a shell."""
import json
import socket

def request(action, **payload):
    body=json.dumps({"action":action,**payload},ensure_ascii=False).encode()+b"\n"
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(60)
        s.connect("/run/pkvm-lb-agent/control.sock")
        s.sendall(body)
        response=b""
        while b"\n" not in response and len(response)<65536:
            chunk=s.recv(8192)
            if not chunk: break
            response+=chunk
    result=json.loads(response)
    if result.get("error"):
        raise RuntimeError("LB deployment failed: "+result["error"])
    return result
