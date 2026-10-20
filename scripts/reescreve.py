"""Reescritor v2: insere o fluxo dos objetos de sacrificio, em pedacos, logo apos cada troca do G-code dos Jesus,
com o volume tirado da purga daquela troca (a purga na canaleta diminui o mesmo tanto).
Linhas dos Jesus e M620/M621 ficam iguais; muda so os G1 E dentro de FLUSH_START..FLUSH_END e a area da malha da
mesa (G29 A1), que passa a cobrir tambem os objetos.
uso: reescreve.py <jesus.gcode> <objetos.gcode> <saida.gcode> [--manter 0]"""
import math, re, sys

A175 = math.pi * 1.75 ** 2 / 4
INI, FIM = '; SACRIFICIO_INICIO', '; SACRIFICIO_FIM'
N_ROTA, N_SAIDA = 9, 5                # linhas de rota antes do fluxo e de saida depois (o conferidor usa)
JESUS_Y1 = 89.0 + 15                  # borda de tras do bloco dos Jesus + margem: rota passa por tras dele
_CAMPOS = re.compile(r'(?<!\w)([A-Za-z_])(-?[\d.]+)')   # (?<!\w)K = \bK: letra sem letra/numero antes
_ULT = [None, None]


def _num(ln, k):
    """valor do campo k (X, Y, E...) na parte de codigo da linha (antes do ';'), ou None: o PRIMEIRO K que tem
    numero logo depois e nao tem letra/numero antes (o mesmo da versao antiga, re.search com \bK). A linha e lida
    uma vez e os campos ficam guardados: as chamadas seguintes na mesma linha (X, Y, Z, E) saem de graca (19/10)."""
    if ln is not _ULT[0]:
        d = {}
        for kk, v in _CAMPOS.findall(ln.split(';', 1)[0]):
            if kk not in d: d[kk] = v
        _ULT[0], _ULT[1] = ln, d
    v = _ULT[1].get(k)
    return float(v) if v is not None else None


MOV = re.compile(r'^G[0-3]\b')
FAN = re.compile(r'^M106 S([\d.]+)')  # ventoinha da peca (sem P = principal)


def fluxo(path):
    """Linhas dos objetos (sem start/end, sem blocos condicionais M622..M623): movimento, M204 e ventoinha,
    com o estado apos cada uma."""
    linhas, dentro, cond = [], False, 0
    x = y = z = f = None
    acel, extr, ret, fan = 6000, 0.0, 0.0, 0.0
    for ln in open(path, encoding='utf-8', errors='ignore'):
        ln = ln.rstrip('\n')
        if ln.startswith('; CHANGE_LAYER'): dentro = True
        if ln.startswith('; MACHINE_END_GCODE_START'): break
        if not dentro: continue
        if ln.startswith('M622 ') or ln.startswith('M622\t'): cond += 1; continue
        if ln.startswith('M623'): cond = max(0, cond - 1); continue
        if cond: continue
        if ln.startswith('M204'):
            acel = int(_num(ln, 'S') or acel); linhas.append({'ln': ln, 'tipo': 'acel', 'vol': extr, 'ret': round(ret, 4)}); continue
        m = FAN.match(ln)
        if m:
            fan = float(m.group(1)); linhas.append({'ln': ln, 'tipo': 'fan', 'vol': extr, 'ret': round(ret, 4)}); continue
        if not MOV.match(ln): continue
        cod = ln.split(';')[0].strip()
        X, Y, Z, E, F = (_num(cod, k) for k in 'XYZEF')
        x = X if X is not None else x; y = Y if Y is not None else y; z = Z if Z is not None else z; f = F if F is not None else f
        tem_xy = X is not None or Y is not None or cod.startswith(('G2', 'G3'))
        ext = E is not None and E > 0 and tem_xy
        if E is not None and E < 0: ret += E                       # retracao, inclusive a de limpeza (wipe) com XY
        elif E is not None and not tem_xy: ret = 0.0               # volta da retracao (Bambu devolve tudo)
        if ext: extr += E * A175
        linhas.append({'ln': ln, 'tipo': 'ext' if ext else 'mov', 'x': x, 'y': y, 'z': z, 'f': f, 'acel': acel,
                       'vol': extr, 'ret': round(ret, 4), 'fan': fan})
    return linhas


def pedaco(lin, i0, alvo):
    """Indice final (exclusivo) do pedaco que comeca em i0 e soma ~alvo mm3; so corta depois de extrusao sem retracao."""
    base = _vol_antes(lin, i0)
    melhor = None
    for j in range(i0, len(lin)):
        l = lin[j]
        if l['tipo'] != 'ext' or abs(l['ret']) > 0.02: continue      # 0,02 mm: arredondamento das limpezas do fatiador
        v = l['vol'] - base
        if v >= alvo: return j + 1 if (melhor is None or v - alvo <= alvo - melhor[1]) else melhor[0]
        melhor = (j + 1, v)
    return len(lin)


def _vol_antes(lin, i0):
    return lin[i0 - 1]['vol'] if i0 > 0 else 0.0


def _estado_antes(lin, i0):
    """(linha de movimento com x/y/z/f, aceleracao, ventoinha) imediatamente antes da linha i0."""
    est, acel, fan = None, 6000, 0.0
    for j in range(i0 - 1, -1, -1):
        if lin[j]['tipo'] in ('ext', 'mov') and lin[j]['x'] is not None: est = lin[j]; break
    for j in range(i0 - 1, -1, -1):
        if lin[j]['tipo'] == 'acel': acel = int(_num(lin[j]['ln'], 'S')); break
    for j in range(i0 - 1, -1, -1):
        if lin[j]['tipo'] == 'fan': fan = float(FAN.match(lin[j]['ln']).group(1)); break
    return est, acel, fan


def bloco(lin, i0, i1, z_atual, z_obj_max, acel_jesus, fan_jesus, z_cam=0.0, volta=None):
    """Texto do pedaco i0..i1 (com rota segura). Devolve (linhas, novo z_obj_max).
    volta=(x, y): no fim, volta o bico a esse ponto em Z seguro (pedaco de sobra antes do G-code de fim)."""
    est, acel, fan = _estado_antes(lin, i0)
    if est is None: est = next(l for l in lin[i0:i1] if l['tipo'] in ('ext', 'mov') and l['x'] is not None)
    x0, y0, z0, f0 = est['x'], est['y'], est['z'], est['f'] or 3000
    if z0 is None:                                     # 1o pedaco: o fluxo ainda nao disse o Z
        z0 = next(l['z'] for l in lin[i0:i1] if l.get('z') is not None)
    zmax_ped = max([l['z'] for l in lin[i0:i1] if l.get('z') is not None] + [z0])
    zs = max(z_atual, z_obj_max + 1.0, z_cam + 2.0)          # acima do objeto e do topo atual dos Jesus
    out = [INI, f'G1 Z{zs:.3f} F1200', f'G1 Y{max(y0, JESUS_Y1):.3f} F12000', f'G1 X{x0:.3f} F30000', f'G1 Y{y0:.3f} F12000',
           f'G1 Z{z0:.3f} F1200', f'M204 S{acel}', f'M106 S{fan:g}', 'G1 E2 F1800', f'G1 F{f0:.0f}']
    out += [l['ln'] for l in lin[i0:i1]]
    novo_max = max(z_obj_max, zmax_ped)
    ret_fim = lin[i1 - 1]['ret']                       # fluxo terminou retraido (fim do objeto): devolve antes
    zsai = max(z_atual, novo_max + 1.0, z_cam + 2.0)
    out += [f'G1 E{-ret_fim:.4f} F1800' if ret_fim < -1e-4 else 'G4 P0', 'G1 E-2 F1800', f'G1 Z{zsai:.3f} F1200',
            f'M204 S{acel_jesus}', f'M106 S{fan_jesus:g}']
    if volta: out.append(f'G1 X{volta[0]:.3f} Y{volta[1]:.3f} F30000')
    out.append(FIM)
    return out, novo_max


def malha_uniao(lin_jesus, objetos):
    """G29 A1 cobrindo Jesus + objetos (a do fatiamento dos objetos sozinhos ja cobre a regiao deles)."""
    g = next((l for l in open(objetos, encoding='utf-8', errors='ignore') if l.lstrip().startswith('G29 A1 ')), None)
    if not g: return lin_jesus
    a = [_num(lin_jesus, k) for k in 'XYIJ']; b = [_num(g, k) for k in 'XYIJ']
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    recuo = lin_jesus[:len(lin_jesus) - len(lin_jesus.lstrip())]
    return recuo + f'G29 A1 X{x0:.4f} Y{y0:.4f} I{x1 - x0:.4f} J{y1 - y0:.4f} ; malha: Jesus + objetos de sacrificio'


def reescreve(jesus, objetos, saida, manter=0.0):
    lin = fluxo(objetos)
    total_obj = lin[-1]['vol'] if lin else 0.0
    src = open(jesus, encoding='utf-8', errors='ignore').read().split('\n')
    n_trocas = sum(1 for l in src if l.startswith('M620 S') and 'A' in l.split(';')[0])
    out, i_obj, z_atual, z_obj_max, acel_j, z_cam, fan_j = [], 0, 0.0, 0.0, 6000, 0.0, 0.0
    xj = yj = 0.0
    impressao = False                 # so depois da 1a camada: troca do G-code de inicio nao recebe pedaco
    rel = {'trocas': 0, 'pedacos': [], 'objeto_mm3': total_obj, 'sobra_mm3': 0.0}

    def acompanha(l):
        nonlocal z_atual, acel_j, z_cam, fan_j, xj, yj
        if MOV.match(l):
            c = l.split(';')[0]
            z, x, y = _num(c, 'Z'), _num(c, 'X'), _num(c, 'Y')
            if z is not None: z_atual = z
            if x is not None: xj = x
            if y is not None: yj = y
        elif l.startswith('M204'): acel_j = int(_num(l, 'S') or acel_j)
        elif l.startswith('; Z_HEIGHT:'): z_cam = float(l.split(':')[1])
        elif FAN.match(l): fan_j = float(FAN.match(l).group(1))

    k = 0
    while k < len(src):
        ln = src[k]
        if ln.lstrip().startswith('G29 A1 '):
            out.append(malha_uniao(ln, objetos)); k += 1; continue
        acompanha(ln)
        if ln.startswith('; CHANGE_LAYER'): impressao = True
        if ln.startswith('M620 S') and 'A' in ln.split(';')[0]:
            # uma troca: do M620 ao M621; pode ter varios blocos FLUSH
            fim = k
            while not src[fim].startswith('M621 S'): fim += 1
            linhas_e, dentro = [], False
            for m in range(k, fim):
                if src[m].startswith('; FLUSH_START'): dentro = True
                elif src[m].startswith('; FLUSH_END'): dentro = False
                elif dentro and src[m].startswith('G1 E'): linhas_e.append(m)
            purga = sum(_num(src[m], 'E') for m in linhas_e) * A175
            ultima = rel['trocas'] == n_trocas - 1
            alvo = float('inf') if ultima else max(0.0, purga - manter)   # na ultima troca vai todo o resto
            if impressao and i_obj < len(lin) and alvo > 1:
                i1 = pedaco(lin, i_obj, alvo)
                vol = lin[i1 - 1]['vol'] - _vol_antes(lin, i_obj)
            else:
                i1, vol = i_obj, 0.0
            usar = min(vol, purga)
            fator = (purga - usar) / purga if purga > 0 else 1.0
            for m in range(k, fim + 1):
                l = src[m]
                if m in linhas_e:
                    e = _num(l, 'E') * fator
                    if e > 1e-4: out.append(re.sub(r'E[\d.]+', f'E{e:.5f}', l, count=1))
                    continue
                if m > k: acompanha(l)
                out.append(l)
            # depois do M621 vem o 'filament start gcode'; o pedaco entra antes da 1a viagem/objeto dos Jesus
            m = fim + 1
            while m < len(src) and not (src[m].startswith('; OBJECT_ID') or src[m].startswith('; start printing object')
                                        or MOV.match(src[m]) or src[m].startswith('; CHANGE_LAYER')):
                acompanha(src[m]); out.append(src[m]); m += 1
            if i1 > i_obj:
                txt, z_obj_max = bloco(lin, i_obj, i1, z_atual, z_obj_max, acel_j, fan_j, z_cam)
                out += txt
            rel['trocas'] += 1
            rel['pedacos'].append({'z_jesus': z_cam, 'purga': purga, 'pedaco': vol, 'tirado': usar, 'z_obj_max': z_obj_max})
            if ultima and vol > usar: rel['sobra_mm3'] = vol - usar
            i_obj = i1
            k = m
            continue
        if ln.startswith('; MACHINE_END_GCODE_START') and i_obj < len(lin):
            # so acontece sem troca nenhuma: imprime o resto e volta o bico para onde estava
            txt, z_obj_max = bloco(lin, i_obj, len(lin), z_atual, z_obj_max, acel_j, fan_j, z_cam, volta=(xj, yj))
            out += txt
            rel['sobra_mm3'] = total_obj - _vol_antes(lin, i_obj)
            i_obj = len(lin)
        out.append(ln)
        k += 1
    open(saida, 'w', encoding='utf-8', newline='\n').write('\n'.join(out))
    return rel


if __name__ == '__main__':
    a = sys.argv[1:]
    manter = float(a[a.index('--manter') + 1]) if '--manter' in a else 0.0
    r = reescreve(a[0], a[1], a[2], manter)
    tir = sum(p['tirado'] for p in r['pedacos']); pur = sum(p['purga'] for p in r['pedacos'])
    print(f"trocas {r['trocas']}  purga {pur * 1.27e-3:.2f} g  tirada da canaleta {tir * 1.27e-3:.2f} g  "
          f"objeto {r['objeto_mm3'] * 1.27e-3:.2f} g  sobra (apos a ultima troca) {r['sobra_mm3'] * 1.27e-3:.2f} g")
