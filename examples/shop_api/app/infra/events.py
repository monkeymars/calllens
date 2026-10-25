from app.infra import log

_subs = {}

def publish(topic, payload):
    log.info(f"event {topic}")
    for fn in _subs.get(topic, []):
        fn(payload)

def subscribe(topic, fn):
    _subs.setdefault(topic, []).append(fn)
