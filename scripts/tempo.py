"""Tempo real da impressao depois do zera purga, para a impressora mostrar o tempo certo (linhas M73 P<%> R<min>).

O Studio calcula o tempo ANTES do pos-processamento; as linhas M73 que ele grava sao o que a tela da A1 mostra. Aqui:
1. modelo simples de tempo por linha: distancia / velocidade (F), extrusao parada E / F, pausas (M400 S, G4) e um custo
   fixo por troca de cor (descarga, carga, limpeza, aquecimento: o que o modelo nao enxerga);
2. o custo fixo por troca e MEDIDO no proprio arquivo: tempo do Studio entre o M73 antes e depois de cada troca menos o
   que o modelo da para o mesmo trecho (mediana de todas as trocas);
3. fator de calibracao = tempo total do Studio / tempo do modelo no G-code original;
4. tempo novo = modelo no G-code novo x fator; cada M73 do G-code novo e refeito com o que falta dali ate o fim.
Nada alem das linhas M73 e das duas linhas de tempo do cabecalho muda (refaz_m73 confere isso)."""
import math, re

MOV = re.compile(r'^G[0-3] ')
NUM = re.compile(r'([XYZEFRIJ])(-?\d*\.?\d+)')
M73 = re.compile(r'^M73 P(\d+) R(\d+)')


def _pausa(l):
    m = re.match(r'^M400 S(\d+(?:\.\d+)?)', l)
    if m: return float(m.group(1))
    m = re.match(r'^G4 (?:S(\d+(?:\.\d+)?)|P(\d+))', l)
    if m: return float(m.group(1)) if m.group(1) else float(m.group(2)) / 1000
    return 0.0


def acumulado(linhas):
    """(xyz, fixo): tempo (s) acumulado ANTES de cada linha, len+1 valores. xyz = deslocamentos do bico (o modelo ignora
    aceleracao, por isso sao calibrados); fixo = extrusao parada (purga, E/F exato) + pausas (M400 S, G4)."""
    x = y = z = 0.0; f = 3000.0; a = b = 0.0
    xyz, fixo = [0.0] * (len(linhas) + 1), [0.0] * (len(linhas) + 1)
    for i, l in enumerate(linhas):
        xyz[i], fixo[i] = a, b
        if not l or l[0] not in 'GM': continue
        if MOV.match(l):
            v = dict(NUM.findall(l.split(';', 1)[0]))
            if 'F' in v:
                try: f = max(1.0, float(v['F']))
                except ValueError: pass
            nx, ny, nz = (float(v[k]) if k in v else d for k, d in (('X', x), ('Y', y), ('Z', z)))
            d = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2)
            if d > 0: a += d / (f / 60.0)
            elif 'E' in v: b += abs(float(v['E'])) / (f / 60.0)
            x, y, z = nx, ny, nz
        elif l.startswith(('M400 S', 'G4 ')):
            b += _pausa(l)
    xyz[len(linhas)], fixo[len(linhas)] = a, b
    return xyz, fixo


def tempo_studio(linhas):
    m = re.search(r'total estimated time: (?:(\d+)d )?(?:(\d+)h )?(?:(\d+)m )?(\d+)s', '\n'.join(linhas[:60]))
    return sum(int(v or 0) * k for v, k in zip(m.groups(), (86400, 3600, 60, 1))) if m else None


def custo_por_troca(orig, eh_troca, ac=None):
    """mediana de (tempo do Studio pela M73 entre antes e depois da troca) - (purga e pausas exatas no mesmo trecho):
    o que a troca custa alem disso (descarga, carga, corte, aquecimento, idas a canaleta)."""
    import bisect
    _, fixo = acumulado(orig) if ac is None else ac
    marcas = [(i, int(m.group(2))) for i, l in enumerate(orig) for m in [M73.match(l)] if m]
    pos = [m[0] for m in marcas]
    custos = []
    for k, l in enumerate(orig):
        if not eh_troca(l): continue
        fim = k
        while fim < len(orig) and not orig[fim].startswith('M621 S'): fim += 1
        ia, id_ = bisect.bisect_left(pos, k) - 1, bisect.bisect_right(pos, fim)
        if ia < 0 or id_ >= len(marcas): continue
        (pa, ra), (pd, rd) = marcas[ia], marcas[id_]
        custos.append((ra - rd) * 60.0 - (fixo[pd] - fixo[pa]))
    if not custos: return 0.0
    custos.sort()
    return max(0.0, custos[len(custos) // 2])


def prepara_orig(orig, eh_troca):
    """parte do calculo que so depende do original (pode rodar em paralelo com o resto)."""
    t_antes = tempo_studio(orig)
    xo, fo = acumulado(orig)
    c = custo_por_troca(orig, eh_troca, (xo, fo))
    return t_antes, xo[-1], fo[-1], c, sum(1 for l in orig if eh_troca(l))


def m73_novo(novo, eh_troca, prep):
    """{indice da linha M73: texto novo}, linha nova do cabecalho e tempo depois (s)."""
    t_antes, xo_tot, fo_tot, c, n_orig = prep
    xn, fn = acumulado(novo)
    trocas_antes, k = [0] * (len(novo) + 1), 0
    for i, l in enumerate(novo):
        trocas_antes[i] = k
        if eh_troca(l): k += 1
    trocas_antes[len(novo)] = k
    fator = (t_antes - n_orig * c - fo_tot) / xo_tot if xo_tot > 0 else 1.0
    gasto = lambda i: xn[i] * fator + fn[i] + trocas_antes[i] * c
    t_depois = gasto(len(novo))
    hm = lambda s: f'{int(s // 3600)}h {int(s % 3600 // 60)}m {int(s % 60)}s'
    trocas = {}
    for i, l in enumerate(novo):
        if M73.match(l):
            g = gasto(i)
            trocas[i] = f'M73 P{min(100, int(100 * g / t_depois)) if t_depois else 0} R{int(round(max(0.0, t_depois - g) / 60))}'
        elif i < 60 and 'total estimated time:' in l:
            trocas[i] = re.sub(r'total estimated time: .*$', f'total estimated time: {hm(t_depois)}', l)
    return trocas, t_depois, {'custo_troca_s': c, 'fator_desloc': fator}


def aplica_m73(novo, trocas):
    saida = list(novo)
    for i, l in trocas.items(): saida[i] = l
    # so M73 e a linha de tempo do cabecalho podem ter mudado
    dif = [i for i, (a, b) in enumerate(zip(novo, saida)) if a != b and not (M73.match(a) or 'total estimated time' in a)]
    if dif or len(saida) != len(novo): raise ValueError(f'refaz_m73 mudou linhas que nao devia: {dif[:3]}')
    return saida


def refaz_m73(orig, novo, eh_troca):
    """-> (novo com M73 e cabecalho refeitos, tempo_antes_s, tempo_depois_s, info). So os deslocamentos sao calibrados:
    fator = (Studio - trocas x custo - purga e pausas exatas) / deslocamentos do modelo, medido no original."""
    prep = prepara_orig(orig, eh_troca)
    if not prep[0]: return novo, None, None, {}
    trocas, t_depois, info = m73_novo(novo, eh_troca, prep)
    return aplica_m73(novo, trocas), prep[0], t_depois, info
