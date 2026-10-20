"""Poe o objeto de sacrificio num lugar seguro da placa (projeto .3mf do Studio, ANTES de fatiar).

uso: posiciona_sacrificio.py projeto.3mf [--escala auto|0.8] [--saida X.3mf]

Regra (a mesma do conferidor, confere3.py): o objeto de sacrificio e impresso fora do ritmo das outras pecas e pode
ficar mais de 25 mm mais alto ou mais baixo que elas; entao ele precisa ficar a >= 76,5 mm delas em Y (barra X da
cabeca, 56,5 mm + margem) e a >= 73 mm em qualquer direcao (raio da cabeca). Medido no G-code; aqui se mede pela
malha + MARGEM_FATIADO de cada lado (o que o fatiador poe em volta) + 0,5 mm; o zera_purga confere de novo no G-code.

1. Se ja esta seguro: nao grava nada.
2. Se cabe atras ou na frente das outras pecas no tamanho atual: move e grava <projeto>_posicionado.3mf.
3. Se nao cabe: diz a maior escala que cabe. Com --escala auto (ou um numero) aplica, desde que >= ESCALA_MIN
   (faixa aprovada pelo dono: 0,7 a 1,3 do tamanho atual). Abaixo disso, sugere trocar o objeto e diz a base maxima.
O arquivo de origem nunca e alterado. Objeto de sacrificio = nome com 'sacrif' ou 'purga' (como no zera_purga)."""
import json, os, re, sys, zipfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from le3mf import le_itens

FOLGA_Y, FOLGA_RAIO = 76.5, 73.0         # confere3: BARRA_Y + BARRA_MARGEM, FOLGA_CABECA
MARGEM_FATIADO = 1.5                     # malha -> extrusao no G-code em Y (placa Maria: Marias 51,6 -> 52,8; prateleira -0,3)
BORDA = 8.0                              # longe da beira da mesa (o CLI recusa menos que isso)
ESCALA_MIN = 0.70


def nomes(z):
    ms = z.read('Metadata/model_settings.config').decode()
    return {m.group(1): m.group(2) for m in re.finditer(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"', ms)}


def mesa(z):
    d = json.loads(z.read('Metadata/project_settings.config'))
    pts = np.array([[float(c) for c in p.split('x')] for p in d.get('printable_area', ['0x0', '256x0', '256x256', '0x256'])])
    return pts.min(0), pts.max(0)


def caixa(v):
    return np.r_[v[:, :2].min(0), v[:, :2].max(0)]           # x0, y0, x1, y1


def folga(a, b):
    """(folga em Y, distancia entre retangulos) de duas caixas x0,y0,x1,y1."""
    gy = max(b[1] - a[3], a[1] - b[3])
    gx = max(b[0] - a[2], a[0] - b[2])
    return gy, float(np.hypot(max(gx, 0), max(gy, 0)))


def planeja(outros, sac, lo, hi, escala_pedida=None):
    """-> dict com 'acao' (ok | mover | escala | trocar), escala k, deslocamento (dx, dy) e o motivo."""
    m = 2 * MARGEM_FATIADO + 0.5
    gy, dist = folga(outros, sac)
    if gy >= FOLGA_Y + m and dist >= FOLGA_RAIO + m:
        return {'acao': 'ok', 'k': 1.0, 'd': (0.0, 0.0), 'folga_y': gy}
    W, D = sac[2] - sac[0], sac[3] - sac[1]
    x0, x1 = lo[0] + BORDA + MARGEM_FATIADO, hi[0] - BORDA - MARGEM_FATIADO
    faixas = [('atras', outros[3] + FOLGA_Y + m, hi[1] - BORDA - MARGEM_FATIADO),
              ('frente', lo[1] + BORDA + MARGEM_FATIADO, outros[1] - FOLGA_Y - m)]
    melhor = max(faixas, key=lambda f: min((f[2] - f[1]) / D, (x1 - x0) / W))
    nome, a, b = melhor
    k_cabe = min((b - a) / D, (x1 - x0) / W)
    if escala_pedida is not None:
        k = k_cabe if escala_pedida == 'auto' else float(escala_pedida)
        k = min(k, 1.0, k_cabe)
    else:
        k = 1.0
    plano = {'faixa': nome, 'k_cabe': k_cabe, 'base_max': (x1 - x0, b - a)}
    if k_cabe < ESCALA_MIN:
        return {**plano, 'acao': 'trocar', 'k': None}
    if k_cabe < 1.0 and escala_pedida is None:
        return {**plano, 'acao': 'escala', 'k': None}
    k = np.floor(k * 100) / 100
    if k < ESCALA_MIN: return {**plano, 'acao': 'trocar', 'k': None}
    cx = np.clip((sac[0] + sac[2]) / 2, x0 + W * k / 2, x1 - W * k / 2)
    cy = b - D * k / 2 if nome == 'atras' else a + D * k / 2      # encosta na beira longe das pecas
    return {**plano, 'acao': 'mover', 'k': float(k), 'centro': (float(cx), float(cy))}


def _mat(t):
    v = [float(x) for x in t.split()]
    return np.array(v[:9]).reshape(3, 3).T, np.array(v[9:12])


def _txt(L, t):
    return ' '.join(f'{x:.9g}' for x in list(L.T.reshape(-1)) + list(t))


def main():
    a = sys.argv[1:]
    opt = {'--escala': None, '--saida': None}
    for k in list(opt):
        if k in a: i = a.index(k); opt[k] = a[i + 1]; del a[i:i + 2]
    if not a or not os.path.exists(a[0]):
        sys.exit(f'arquivo nao encontrado: {a[0] if a else "(nenhum)"}. Passe o projeto .3mf (o que voce abre no Studio, '
                 'nao o .gcode.3mf).')
    ent = a[0]
    z = zipfile.ZipFile(ent)
    nm = nomes(z)
    itens = le_itens(ent)
    for it in itens: it['nome'] = nm.get(it['id'], it['nome'])
    sac = [it for it in itens if re.search(r'sacrif|purga', it['nome'], re.I)]
    if len(sac) != 1:
        sys.exit(f"preciso de exatamente 1 objeto com 'sacrificio' ou 'purga' no nome; achei {len(sac)}. "
                 f"Objetos da placa: {sorted({it['nome'] for it in itens})}")
    s = sac[0]
    outros = [it for it in itens if it is not s]
    bo = np.r_[np.min([caixa(it['v'])[:2] for it in outros], 0), np.max([caixa(it['v'])[2:] for it in outros], 0)]
    bs = caixa(s['v'])
    lo, hi = mesa(z)
    p = planeja(bo, bs, lo, hi, opt['--escala'])
    alt = s['v'][:, 2].max() - s['v'][:, 2].min()
    print(f"outras pecas: X {bo[0]:.1f}..{bo[2]:.1f}  Y {bo[1]:.1f}..{bo[3]:.1f}; objeto '{s['nome']}': "
          f"{bs[2] - bs[0]:.1f} x {bs[3] - bs[1]:.1f} mm, altura {alt:.1f} mm")
    if p['acao'] == 'ok':
        print(f"ja esta seguro (folga em Y {p['folga_y']:.1f} mm). Nada gravado."); return
    if p['acao'] == 'escala':
        sys.exit(f"NAO CABE no tamanho atual. Cabe com escala {np.floor(p['k_cabe'] * 100) / 100:.2f} ({p['faixa']} das pecas). "
                 f"Para aplicar: --escala auto. Ou troque por um objeto com base de ate {p['base_max'][0]:.0f} x {p['base_max'][1]:.0f} mm.")
    if p['acao'] == 'trocar':
        sys.exit(f"NAO CABE nem com escala {ESCALA_MIN} (precisaria {p['k_cabe']:.2f}). Troque o objeto de sacrificio por um com "
                 f"base de ate {p['base_max'][0]:.0f} x {p['base_max'][1]:.0f} mm, ou tire pecas da placa.")
    # mover (e talvez reduzir): novo = k * (antigo - t) + t'
    k = p['k']
    L, t = _mat(s['transform'])
    v = k * (s['v'] - t)
    cx, cy = p['centro']
    t2 = np.array([cx - (v[:, 0].min() + v[:, 0].max()) / 2, cy - (v[:, 1].min() + v[:, 1].max()) / 2, -v[:, 2].min()])
    novo = _txt(k * L, t2)
    nb = caixa(v + t2)
    gy, dist = folga(bo, nb)
    m = z.read('3D/3dmodel.model').decode()
    m2 = re.sub(rf'(<item objectid="{s["id"]}"[^>]*transform=")[^"]+', lambda r: r.group(1) + novo, m, count=1)
    ms = z.read('Metadata/model_settings.config').decode()
    ms2 = re.sub(rf'(<assemble_item object_id="{s["id"]}" instance_id="0" transform=")[^"]+', lambda r: r.group(1) + novo, ms, count=1)
    assert m2 != m, 'nao achei o item do objeto no 3dmodel.model'
    saida = opt['--saida'] or re.sub(r'\.3mf$', '', ent) + '_posicionado.3mf'
    zo = zipfile.ZipFile(saida, 'w', zipfile.ZIP_DEFLATED)
    for it in z.infolist():
        d = z.read(it.filename)
        if it.filename == '3D/3dmodel.model': d = m2.encode()
        elif it.filename == 'Metadata/model_settings.config': d = ms2.encode()
        zo.writestr(it, d)
    zo.close()
    print(f"OK: {saida}\n  objeto {'reduzido para ' + format(k, '.2f') + ' e ' if k < 1 else ''}movido para {p['faixa']}: "
          f"X {nb[0]:.1f}..{nb[2]:.1f}  Y {nb[1]:.1f}..{nb[3]:.1f}; folga em Y {gy:.1f} mm (minimo {FOLGA_Y + 2 * MARGEM_FATIADO + 0.5:.1f} pela malha)."
          "\n  Abra no Studio, confira, fatie, exporte o .gcode.3mf e rode zera_purga.py nele.")


if __name__ == '__main__':
    main()
