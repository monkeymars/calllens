import hashlib, json, base64

def verify(password, password_hash):
    return hashlib.sha256(password.encode()).hexdigest() == password_hash

def sign(payload):
    return base64.b64encode(json.dumps(payload).encode()).decode()

def unsign(token):
    return json.loads(base64.b64decode(token))
