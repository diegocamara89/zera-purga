"""Le um 3MF (Bambu ou comum) e devolve cada item da mesa como malha pronta (vertices em mm, triangulos).
Stdlib + numpy. uso: le3mf.py arquivo.3mf  -> tabela de itens."""
import re, sys, zipfile
import numpy as np
import xml.etree.ElementTree as ET

NS = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
PATH = '{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path'


def _mat(t):
    """transform 3MF 'a b c d e f g h i x y z' (linha-vetor) -> matriz 4x4 coluna-vetor."""
    v = [float(x) for x in t.split()] if t else [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    M = np.eye(4)
    M[:3, :3] = np.array(v[:9]).reshape(3, 3).T
    M[:3, 3] = v[9:12]
    return M


def le_itens(arq):
    """[{'nome', 'v': (n,3), 'f': (m,3)}] um por item <build>, transformacoes aplicadas."""
    z = zipfile.ZipFile(arq)
    docs = {}

    def doc(p):
        p = p.lstrip('/')
        if p not in docs:
            r = ET.fromstring(z.read(p))
            docs[p] = {o.get('id'): o for o in r.iterfind('.//m:resources/m:object', NS)}, r
        return docs[p]

    def malha(p, oid, M):
        objs, _ = doc(p)
        o = objs[oid]
        mesh = o.find('m:mesh', NS)
        if mesh is not None:
            v = np.array([[float(x.get(k)) for k in 'xyz'] for x in mesh.iterfind('m:vertices/m:vertex', NS)])
            f = np.array([[int(x.get(k)) for k in ('v1', 'v2', 'v3')] for x in mesh.iterfind('m:triangles/m:triangle', NS)])
            return [((np.c_[v, np.ones(len(v))] @ M.T)[:, :3], f)]
        out = []
        for c in o.iterfind('m:components/m:component', NS):
            out += malha((c.get(PATH) or p), c.get('objectid'), M @ _mat(c.get('transform')))
        return out

    principal = '3D/3dmodel.model'
    objs, raiz = doc(principal)
    itens = []
    for it in raiz.iterfind('.//m:build/m:item', NS):
        partes = malha(principal, it.get('objectid'), _mat(it.get('transform')))
        v = np.vstack([p[0] for p in partes])
        f = np.vstack([p[1] + sum(len(q[0]) for q in partes[:i]) for i, p in enumerate(partes)])
        nome = objs[it.get('objectid')].get('name') or f"obj{it.get('objectid')}"
        itens.append({'nome': nome, 'v': v, 'f': f, 'id': it.get('objectid'), 'transform': it.get('transform')})
    return itens


def volume(v, f):
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return float(np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6)


if __name__ == '__main__':
    for i in le_itens(sys.argv[1]):
        ext = i['v'].max(0) - i['v'].min(0)
        print(f"{i['nome'][:30]:30} tri={len(i['f']):7} ext={np.round(ext, 1)} vol={volume(i['v'], i['f']) / 1000:.2f} cm3")
