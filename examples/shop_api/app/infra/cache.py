_store = {}

def get(key):
    return _store.get(key)

def set(key, value, ttl=None):
    _store[key] = value
