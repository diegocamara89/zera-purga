"""Conferidor da v3 (placa unica). Compara o G-code reescrito com o original fatiado no Studio.
confere(original, rotulos, novo, escorva) -> dict; 'ok' so com todas as checagens."""
import math, re
import purga3 as P
from reescreve import A175, _num, MOV

FOLGA_CABECA, BARRA_Y, BARRA_MARGEM, BARRA_Z = 73.0, 56.5, 20.0, 25.0


def tem_xy(c):
    return _num(c, 'X') is not None or _num(c, 'Y') is not None or c.startswith(('G2', 'G3'))


def normaliza(ls):
    """tira blocos inseridos e os G1 E das purgas; desfaz a troca M109->M104. -> (linhas, blocos, purga por troca, n M104)"""
    resto, blocos, purgas, dentro, flush, cur, n104 = [], [], [], False, False, None, 0
    for ln in ls:
        if ln == P.INI: dentro, cur = True, []; continue
        if ln == P.FIM: dentro = False; blocos.append(cur); continue
        if dentro: cur.append(ln); continue
        if P.eh_troca(ln): purgas.append(0.0)
        if ln.startswith('; FLUSH_START'): flush = True
        elif ln.startswith('; FLUSH_END'): flush = False
        elif flush and ln.startswith('G1 E'): purgas[-1] += _n(ln) * A175; continue
        if ln.startswith('M104 S') and P.TAG in ln and 'espera movida' in ln:
            resto.append(f'M109 S{int(_num(ln, "S"))}'); n104 += 1; continue
        resto.append(ln)
    return resto, blocos, purgas, n104


def _n(ln):
    return _num(ln, 'E')


def vol_ext(ls):
    v = 0.0
    for ln in ls:
        c = ln.split(';')[0]
        if MOV.match(c):
            e = _num(c, 'E')
            if e and e > 0 and tem_xy(c): v += e * A175
    return v


def miolo(bl):
    a, b = bl.index(P.FLX_INI), bl.index(P.FLX_FIM)
    return bl[a + 1:b], bl[:a] + bl[b + 1:]


def caixa(ls, so_rotulo=True):
    xs, ys, x, y, lab = [], [], None, None, None
    for ln in ls:
        if P.ROT_START.match(ln) or ln.startswith('; OBJECT_ID'): lab = 1
        elif P.ROT_STOP.match(ln): lab = None
        if ln[:1] != 'G': continue                    # atalho: so linha de movimento interessa
        c = ln.split(';')[0]
        if not MOV.match(c): continue
        X, Y, E = _num(c, 'X'), _num(c, 'Y'), _num(c, 'E')
        x = X if X is not None else x; y = Y if Y is not None else y
        if (lab or not so_rotulo) and E and E > 0 and x is not None and 0 < x < 256 and 0 < y < 256: xs.append(x); ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def dist_ret(a, b):
    return math.hypot(max(b[0] - a[2], a[0] - b[2], 0), max(b[1] - a[3], a[1] - b[3], 0))


def seg_cruza(p, q, r):
    n = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / 0.5))
    return any(r[0] <= p[0] + (q[0] - p[0]) * t / n <= r[2] and r[1] <= p[1] + (q[1] - p[1]) * t / n <= r[3] for t in range(n + 1))


def estado_jesus(ls, rotulos):
    """retracao do extrusor em cada linha que extruda nas outras pecas (zerada no fim de cada troca; o miolo da troca, carga e purga, nao conta).
    Independe de como os trechos foram tirados: se o reescritor mudar o estado que um Jesus encontra, a lista muda."""
    out, s, lab, troca = [], 0.0, None, False
    for ln in ls:
        if P.eh_troca(ln): troca = True
        if ln.startswith('M621 S'): troca, s = False, 0.0      # a troca (carga e purga) termina aqui: estado zerado
        if troca: continue
        m = P.ROT_START.match(ln)
        if m: lab = int(m.group(1))
        elif P.ROT_STOP.match(ln): lab = None
        if ln[:1] != 'G': continue                    # atalho: so linha de movimento interessa
        c = ln.split(';')[0]
        if not MOV.match(c) or 'escorva' in ln: continue
        e = _num(c, 'E')
        if e is None: continue
        if e > 0 and tem_xy(c):
            if lab is not None and lab not in rotulos: out.append(round(s, 2))
        else: s += e
    return out


def filamentos_jesus(ls, rotulos):
    """filamento carregado (ultimo M620 S<n>A) em cada linha que extruda nas outras pecas."""
    out, fil, lab, troca, bloco = [], None, None, False, False
    for ln in ls:
        m = P.FIL.match(ln)
        if m: fil = int(m.group(1)); troca = True
        if ln.startswith('M621 S'): troca = False
        if ln == P.INI: bloco = True
        elif ln == P.FIM: bloco = False
        m = P.ROT_START.match(ln)
        if m: lab = int(m.group(1))
        elif P.ROT_STOP.match(ln): lab = None
        if ln[:1] != 'G' or troca or bloco: continue  # atalho: so linha de movimento fora de troca e bloco
        c = ln.split(';')[0]
        if not (MOV.match(c) and tem_xy(c) and (_num(c, 'E') or 0) > 0) or troca or bloco: continue
        if lab is None or lab not in rotulos: out.append(fil)       # outras pecas e extrusao sem rotulo (saia, torre)
    return out


def parte_purga_base(base):
    """lado do G-code esperado: (resumo do resto, purga por troca)."""
    rb, _, pb, _ = normaliza(base)
    return resumo_lista(rb), pb


def parte_purga_novo(novo, escorva):
    """lado do G-code novo: tudo o que a conferencia da purga precisa dele."""
    rn, blocos, pn, n104 = normaliza(novo)
    ped, t, dentro_b, flush, apos_m621, impressao, mal, esperas = [0.0] * len(pn), -1, False, False, False, False, 0, 0
    for ln in novo:
        if ln.startswith('; CHANGE_LAYER'): impressao = True
        if P.eh_troca(ln): t += 1; apos_m621 = False
        if ln.startswith('M621 S'): apos_m621 = True
        if ln.startswith('; FLUSH_START'): flush = True
        if ln.startswith('; FLUSH_END'): flush = False
        if ln == P.INI:
            dentro_b, cur = True, []
            if not (impressao and apos_m621 and not flush): mal += 1
        elif ln == P.FIM:
            dentro_b = False; mi, _ = miolo(cur); ped[t] += vol_ext(mi)
            esperas += any(l.startswith('M109 S') and P.TAG in l for l in cur)
        elif dentro_b: cur.append(ln)
    fl = []
    for bl in blocos: fl += miolo(bl)[0]
    bal = []
    for bl in blocos:
        s = 0.0
        for ln in bl:
            c = ln.split(';')[0]
            if MOV.match(c) and 'escorva' not in ln:
                e = _num(c, 'E')
                if e is not None and (e < 0 or not tem_xy(c)): s += e
        bal.append(abs(s))
    return {'rn': resumo_lista(rn), 'pn': pn, 'n104': n104, 'so': len(P.trocas_so_do_objeto(rn)),
            'ns': [b['n'] for b in P.blocos_troca(rn)], 'ped': ped, 'mal': mal, 'esperas': esperas, 'fl': resumo_lista(fl),
            'bal': max(bal, default=0.0), 'n_blocos': len(blocos),
            'comp': sum(_num(l, 'E') for l in novo if l.startswith('G1 E') and 'Compensate' in l) * A175 * 1.27e-3}


def junta_purga(lado_base, lado_novo, trechos, escorva, tab=None, desc=None):
    """mesmo dicionario que a parte_purga devolvia, a partir das duas metades."""
    rb, pb = lado_base
    n = lado_novo
    pn = n['pn']
    res = {'resto_identico': rb == n['rn'], 'trocas': (len(pb), len(pn)), 'trocas_so_do_objeto': n['so']}
    carr, mesmo, pior_m = None, 0, 0.0
    for j, nn in enumerate(n['ns']):
        if carr is not None and carr == nn: mesmo += 1
        elif tab and carr is not None and j < len(pb): pior_m = max(pior_m, abs(pb[j] - (desc[0] * tab[0][carr][nn] + desc[1])))
        carr = nn
    res['trocas_para_o_mesmo_filamento'], res['pior_dif_matriz_mm3'] = mesmo, pior_m
    res['blocos_fora_do_lugar'] = n['mal']
    res['esperas_movidas'] = (n['n104'], n['esperas'])
    ped = n['ped']
    difs = [abs(pb[i] - pn[i] - min(ped[i], pb[i])) for i in range(min(len(pb), len(pn)))]
    res['pior_dif_troca_mm3'] = max(difs) if difs else 0.0
    res['objeto_completo'] = n['fl'] == resumo_lista(trechos)
    res['pior_balanco_e_mm'] = n['bal']
    res['escorva_g'] = n['n_blocos'] * escorva * A175 * 1.27e-3
    res['compensacao_fixa_g'] = n['comp']
    res['objeto_g'] = vol_ext(trechos) * 1.27e-3
    res['canaleta_antes_g'], res['canaleta_depois_g'] = sum(pb) * 1.27e-3, sum(pn) * 1.27e-3
    res['absorvido_g'] = res['canaleta_antes_g'] - res['canaleta_depois_g']
    res['sobra_fim_g'] = max(0.0, res['objeto_g'] - res['absorvido_g'])
    return res


def parte_purga(base, novo, trechos, escorva, tab=None, desc=None):
    """resto identico, trocas, purga por troca, blocos no lugar, objeto inteiro, balanco do extrusor, gramas."""
    return junta_purga(parte_purga_base(base), parte_purga_novo(novo, escorva), trechos, escorva, tab, desc)


def parte_geometria(base, trechos, novo, bj=None):
    """folgas do objeto as pecas e rotas do bico (barra X, raio da cabeca, viagens baixas). bj = caixa(base) pronta."""
    res = {}
    bj, bo = (caixa(base) if bj is None else bj), caixa(trechos, so_rotulo=False)
    res['dist_obj_jesus_mm'] = dist_ret(bj, bo)
    res['folga_y_barra_mm'] = max(bo[1] - bj[3], bj[1] - bo[3])
    rj = (bj[0] - 5, bj[1] - 5, bj[2] + 5, bj[3] + 5)
    zj, pior_dz, cruza, rota_baixa, obj_max, x, y, z, dentro_b, ini_max, sem_volta = 0.0, 0.0, 0, 0, 0.0, None, None, 0.0, False, 0.0, 0
    for n, ln in enumerate(novo):
        if ln == P.INI: dentro_b, ini_max = True, obj_max; continue
        if ln == P.FIM:
            dentro_b = False
            if novo[n + 1].startswith('; MACHINE_END_GCODE_START') and 'volta' not in novo[n - 1]: sem_volta += 1
            continue
        if ln[:1] != 'G': continue                    # atalho: so linha de movimento interessa
        c = ln.split(';')[0]
        if not MOV.match(c): continue
        X, Y, Z, E = _num(c, 'X'), _num(c, 'Y'), _num(c, 'Z'), _num(c, 'E')
        nx, ny, nz = (X if X is not None else x), (Y if Y is not None else y), (Z if Z is not None else z)
        if dentro_b:
            if 'rota' in ln and (X is not None or Y is not None) and nz < max(ini_max + 0.9, zj + 1.9): rota_baixa += 1
            if 'saida' in ln and nz < max(obj_max + 0.9, zj + 1.9): rota_baixa += 1
            if E and E > 0 and (X is not None or Y is not None) and P.TAG not in ln:
                pior_dz = max(pior_dz, abs(nz - zj)); obj_max = max(obj_max, nz)
            if x is not None and nx is not None and (nx, ny) != (x, y) and min(z, nz) < zj + 1.5 and seg_cruza((x, y), (nx, ny), rj):
                cruza += 1
        if not dentro_b and E and E > 0 and (X is not None or Y is not None): zj = max(zj, nz)   # topo ja impresso das outras pecas
        x, y, z = nx, ny, nz
    res.update(dz_max_mm=pior_dz, viagens_baixas_sobre_jesus=cruza, rota_abaixo_do_seguro=rota_baixa, sobra_sem_volta=sem_volta)
    return res


def junta(ea, eb, fa, fb, purga, geo):
    """monta o dicionario do conferidor (mesmas chaves e mesma ordem de antes) e decide 'ok'."""
    res = {'estado_jesus_igual': ea[1] == eb[1] and ea[0] == eb[0], 'linhas_jesus': (ea[0], eb[0]),
           'resto_identico': purga['resto_identico'], 'trocas': purga['trocas'], 'trocas_so_do_objeto': purga['trocas_so_do_objeto'],
           'filamento_jesus_igual': fa[1] == fb[1] and fa[0] == fb[0]}
    for k in ('trocas_para_o_mesmo_filamento', 'pior_dif_matriz_mm3', 'blocos_fora_do_lugar', 'esperas_movidas', 'pior_dif_troca_mm3',
              'objeto_completo', 'pior_balanco_e_mm', 'escorva_g', 'compensacao_fixa_g', 'objeto_g', 'canaleta_antes_g',
              'canaleta_depois_g', 'absorvido_g', 'sobra_fim_g'):
        res[k] = purga[k]
    res.update(geo)
    res['ok'] = (res['filamento_jesus_igual'] and res['trocas_para_o_mesmo_filamento'] == 0 and res['pior_dif_matriz_mm3'] < 0.5
                 and res['resto_identico'] and res['objeto_completo'] and res['trocas_so_do_objeto'] == 0
                 and res['pior_dif_troca_mm3'] < 2.0 and res['blocos_fora_do_lugar'] == 0 and res['pior_balanco_e_mm'] < 0.01
                 and res['estado_jesus_igual'] and res['dist_obj_jesus_mm'] >= FOLGA_CABECA
                 and (res['dz_max_mm'] < BARRA_Z or res['folga_y_barra_mm'] >= BARRA_Y + BARRA_MARGEM)
                 and res['viagens_baixas_sobre_jesus'] == 0 and res['rota_abaixo_do_seguro'] == 0 and res['sobra_sem_volta'] == 0)
    return res


def resumo_lista(lista):
    """(tamanho, md5) de uma lista: comparar duas listas por isto = comparar as listas (passa entre processos barato)."""
    import hashlib
    return len(lista), hashlib.md5(repr(lista).encode()).hexdigest()


def desconto(base_orig, tab):
    return P.desconto_fixo(base_orig, P.blocos_troca(base_orig), *tab) if tab else None


def confere(original, rotulos, novo, escorva, tab=None, pronto=None):
    """pronto = (base_orig, trechos, base_esperada) ja calculados pelo chamador com estas mesmas funcoes (evita refazer;
    o resultado e identico). Sem pronto, calcula aqui. As partes tambem rodam em paralelo (pos_studio)."""
    if pronto:
        base_orig, trechos, base = pronto
    else:
        base, trechos = P.extrai(original, rotulos)
        base_orig = base
        if tab and P.trocas_so_do_objeto(base): base, _ = P.tira_trocas(base, *tab)   # base que o novo deve ter (menos a purga)
    return junta(resumo_lista(estado_jesus(original, rotulos)), resumo_lista(estado_jesus(novo, rotulos)),
                 resumo_lista(filamentos_jesus(original, rotulos)), resumo_lista(filamentos_jesus(novo, rotulos)),
                 parte_purga(base, novo, trechos, escorva, tab, desconto(base_orig, tab)), parte_geometria(base, trechos, novo))


def jesus_listas(ls, rotulos):
    """estado_jesus e filamentos_jesus numa leitura so (mesmas regras, estados separados): -> (lista estado, lista fil)."""
    oe, s, lab_e, troca_e = [], 0.0, None, False
    of, fil, lab_f, troca_f, bloco = [], None, None, False, False
    for ln in ls:
        if P.eh_troca(ln): troca_e = True
        m = P.FIL.match(ln)
        if m: fil = int(m.group(1)); troca_f = True
        if ln.startswith('M621 S'): troca_e, s, troca_f = False, 0.0, False
        if ln == P.INI: bloco = True
        elif ln == P.FIM: bloco = False
        m = P.ROT_START.match(ln)
        if m:
            lab_f = int(m.group(1))
            if not troca_e: lab_e = lab_f
        elif P.ROT_STOP.match(ln):
            lab_f = None
            if not troca_e: lab_e = None
        if ln[:1] != 'G': continue
        c = ln.split(';')[0]
        if not MOV.match(c): continue
        e = _num(c, 'E')
        xy = None
        if not troca_e and e is not None and 'escorva' not in ln:
            xy = tem_xy(c)
            if e > 0 and xy:
                if lab_e is not None and lab_e not in rotulos: oe.append(round(s, 2))
            else: s += e
        if not troca_f and not bloco and (e or 0) > 0:
            if xy is None: xy = tem_xy(c)
            if xy and (lab_f is None or lab_f not in rotulos): of.append(fil)
    return oe, of


def caixas_ambas(ls):
    """P.caixa_jesus(ls) e caixa(ls) numa leitura so: -> (caixa_jesus, caixa)."""
    xa, ya, xb, yb, x, y, la, lb = [], [], [], [], None, None, None, None
    for ln in ls:
        if P.ROT_START.match(ln): la = 1; lb = 1
        elif P.ROT_STOP.match(ln): la = None; lb = None
        elif ln.startswith('; OBJECT_ID'): lb = 1
        if ln[:1] != 'G': continue
        c = ln.split(';')[0]
        if not MOV.match(c): continue
        X, Y, E = _num(c, 'X'), _num(c, 'Y'), _num(c, 'E')
        x = X if X is not None else x; y = Y if Y is not None else y
        if E and E > 0 and x is not None:
            if la: xa.append(x); ya.append(y)
            if lb and 0 < x < 256 and 0 < y < 256: xb.append(x); yb.append(y)
    return (min(xa), min(ya), max(xa), max(ya)), (min(xb), min(yb), max(xb), max(yb))
