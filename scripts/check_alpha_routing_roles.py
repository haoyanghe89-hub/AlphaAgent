"""Offline guard replay on preserved real outputs; does not reinterpret failed records."""

import json
import sqlite3
import sys
from pathlib import Path

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.decision import agents

sys.stdout.reconfigure(encoding='utf8')
root = Path('.phase3-quant-o/alpha-history-real-02')
blobs = LocalContentAddressedBlobStorage(root / 'blobs')
value = lambda r: json.loads(blobs.get_bytes(BlobRef.from_uri(r)))
with sqlite3.connect('file:' + (root / 'metadata.sqlite').resolve().as_posix() + '?mode=ro', uri=True) as db:
    state = json.loads(db.execute('select payload from inv_quant_research_run where run_id=?',(sys.argv[1],)).fetchone()[0])
for call in state['calls']:
    if call.get('role') == 'Analyst' and call.get('response_ref'):
        result = value(call['response_ref'])
        context = value(call['request_ref'])['context']
        try:
            agents.guard_analysis(agents.RoutedAnalysis.model_validate(result), context)
            error = 'PASS under current guard (original state unchanged)'
        except Exception as e:
            error = str(e)
        print(json.dumps(dict(attempt=call['attempt'],error=error,method=result.get('method_assessment'),policy=result.get('policy_assessment'),claims=result['claims']),ensure_ascii=False))
