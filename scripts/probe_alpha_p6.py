"""New real market/manufacturing input, isolated from published reports and model calls."""
import json
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.contracts import canonical
from marketpulse.quant.decision import candidate_sources, market_scan

sys.stdout.reconfigure(encoding="utf8")
stage=Path('.phase3-quant-o/decision-p6-real-01')
blobs=LocalContentAddressedBlobStorage(stage/'probe-blobs')
repo=SimpleNamespace(raw=lambda raw:blobs.put_bytes(raw).ref.uri,blob=lambda value:blobs.put_bytes(canonical(value).encode()).ref.uri,
    raw_bytes=lambda ref:blobs.get_bytes(BlobRef.from_uri(ref)),value=lambda ref:json.loads(blobs.get_bytes(BlobRef.from_uri(ref))))
for name,entity,collect,rebuild in [('market','设备与制造观察范围',market_scan.collect,market_scan.rebuild),('manufacturing','集成电路制造',candidate_sources.collect,candidate_sources.rebuild)]:
    state=dict(request=dict(entity=entity,budget_profile=dict(max_candidates=3,max_seconds=900)),calls=[])
    def save():
        (stage/(name+'-probe-state.json')).write_text(canonical(state),encoding='utf8')
    try:
        snapshot=collect(repo,state,Event(),save,lambda k,d:print(d,flush=True))
        (stage/(name+'-probe-snapshot.json')).write_text(canonical(snapshot),encoding='utf8')
        data=rebuild(repo,snapshot)
        (stage/(name+'-probe-data.json')).write_text(canonical(data),encoding='utf8')
        print(name,dict(calls=len(state['calls']),coverage=data['coverage'],selected=data.get('selected')),flush=True)
    except Exception as error:
        print(name,type(error).__name__,str(error),flush=True)
