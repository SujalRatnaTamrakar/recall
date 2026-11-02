"""Optional offline CPU embeddings; model download is an explicit setup action."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.request
from pathlib import Path

MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
REVISION = '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'
FILES = {'model.onnx':'onnx/model_quint8_avx2.onnx', 'tokenizer.json':'tokenizer.json'}


def prepare_semantic(cache: Path) -> Path:
    folder = cache / 'semantic-model'
    folder.mkdir(parents=True,exist_ok=True)
    for filename, remote in FILES.items():
        target = folder / filename
        if target.is_file(): continue
        temporary = target.with_suffix('.download')
        try:
            with urllib.request.urlopen(f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{remote}',timeout=60) as source, temporary.open('wb') as out:
                while chunk := source.read(1024*1024): out.write(chunk)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    (folder/'model-info.json').write_text(json.dumps({'model':MODEL,'revision':REVISION,
        'license':'Apache-2.0','source':'https://huggingface.co/'+MODEL}),encoding='utf-8')
    return folder


class LocalSemanticIndex:
    def __init__(self, cache: Path):
        self.folder = cache/'semantic-model'
        self._lock = threading.RLock()
        self._session = self._tokenizer = None
        self._vectors = {}
        self.error = None

    @property
    def ready(self):
        return not self.error and all((self.folder/f).is_file() for f in FILES)

    def _encode(self, texts):
        import numpy as np
        if self._session is None:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            self._session = ort.InferenceSession(str(self.folder/'model.onnx'),sess_options=opts,providers=['CPUExecutionProvider'])
            self._tokenizer = Tokenizer.from_file(str(self.folder/'tokenizer.json'))
            self._tokenizer.enable_truncation(max_length=256)
            self._tokenizer.enable_padding()
        tokens = self._tokenizer.encode_batch(texts)
        inputs = {'input_ids':np.array([t.ids for t in tokens],dtype=np.int64),
                  'attention_mask':np.array([t.attention_mask for t in tokens],dtype=np.int64),
                  'token_type_ids':np.array([t.type_ids for t in tokens],dtype=np.int64)}
        output = self._session.run(None,{i.name:inputs[i.name] for i in self._session.get_inputs()})[0]
        mask = inputs['attention_mask'][...,None]
        vectors = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1),1)
        return (vectors / np.maximum(np.linalg.norm(vectors,axis=1,keepdims=True),1e-9)).astype(np.float32)

    def index(self, records, max_new=256):
        if not self.ready: return
        with self._lock:
            pending = []
            for record in records:
                # Exclude arbitrary payload metadata and paths from embeddings.
                text = (getattr(record,'caption','') or record.text)[:1200]
                key = hashlib.sha256(text.encode()).hexdigest()
                if text and key not in self._vectors:
                    pending.append((key,text))
            pending = list(dict(pending).items())[:max_new]
            try:
                for offset in range(0,len(pending),16):
                    batch = pending[offset:offset+16]
                    vectors = self._encode([text for _,text in batch])
                    self._vectors.update({key:vector for (key,_),vector in zip(batch,vectors)})
                # Bound memory for very long sessions. Missing vectors remain
                # searchable through FTS and can be re-encoded on demand.
                while len(self._vectors)>20000: self._vectors.pop(next(iter(self._vectors)))
            except Exception as exc:
                self.error = type(exc).__name__

    def search(self, question, records, limit=16):
        if not self.ready or not question.strip(): return []
        with self._lock:
            self.index(records,max_new=64)
            if self.error: return []
            try:
                query = self._encode([question[:1000]])[0]
                ranked = []
                for record in records:
                    text = (getattr(record,'caption','') or record.text)[:1200]
                    vector = self._vectors.get(hashlib.sha256(text.encode()).hexdigest())
                    if vector is not None:
                        score = float(vector @ query)
                        if score >= .24: ranked.append((record.evidence_id,score))
                return [eid for eid,_ in sorted(ranked,key=lambda r:r[1],reverse=True)[:limit]]
            except Exception as exc:
                self.error = type(exc).__name__
                return []
