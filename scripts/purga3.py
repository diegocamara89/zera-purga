"""v3 (placa unica): a placa e fatiada no Studio ja com o objeto de sacrificio. Este modulo tira os trechos desse
objeto do G-code e os reimprime em pedacos logo apos cada troca, com o volume da purga daquela troca.
Correcoes da 1a impressao: a espera de temperatura sai da canaleta e vai para o fim do pedaco; a retracao da troca e
devolvida inteira e ha uma escorva curta na canaleta antes de ir ao objeto; os pedacos mantem a etiqueta do objeto
(M624/M625), para o 'pular objeto' funcionar."""
import json, re
from reescreve import A175, _num, MOV, FAN, pedaco, _vol_antes

INI, FIM = '; SACRIFICIO_INICIO', '; SACRIFICIO_FIM'
FLX_INI, FLX_FIM = '; SACRIFICIO_FLUXO_INICIO', '; SACRIFICIO_FLUXO_FIM'
TAG = '; sacrificio:'                   # toda linha acrescentada pelo reescritor leva esta marca
RETIRADO = '; SACRIFICIO_RETIRADO'      # lugar de onde saiu um trecho do objeto
ROT_START = re.compile(r'; start printing object, unique label id: (\d+)')
ROT_STOP = re.compile(r'; stop printing object, unique label id: (\d+)')
RET_TROCA = 2.0                         # a troca termina com G1 E-2; a calibracao de fluxo (1o uso da cor) tira mais 2


def eh_troca(ln):
    return ln.startswith('M620 S') and 'A' in ln.split(';')[0]


def _e_nao_ext(ln):
    """E de retracao/volta (movimento sem extrusao); 0 se nao houver."""
    if 'E' not in ln or ln.lstrip()[:1] != 'G': return 0.0   # atalho: sem E na linha ou nao e movimento -> 0
    c = ln.split(';')[0].strip()
    if not MOV.match(c): return 0.0
    e = _num(c, 'E')
    if e is None: return 0.0
    tem_xy = _num(c, 'X') is not None or _num(c, 'Y') is not None or c.startswith(('G2', 'G3'))
    return e if (e < 0 or not tem_xy) else 0.0


def _estados(trecho):
    """(retracao esperada na entrada, retracao deixada na saida) de um trecho: o fatiador extruda com retracao 0."""
    es = [_e_nao_ext(l) for l in trecho]
    ext = [k for k, l in enumerate(trecho) if MOV.match(l) and (_num(l, 'E') or 0) > 0 and _e_nao_ext(l) == 0.0]
    if not ext: return 0.0, sum(es)
    return -sum(es[:ext[0]]), sum(es[ext[-1] + 1:])


def extrai(src, rotulos):
    """-> (base, trechos).
    base: G-code sem os trechos do objeto. No lugar de cada um fica RETIRADO e o estado que ele deixava (M204, M106).
    O saldo de retracao do trecho (ex.: comeca com G1 E.8 e termina sem retrair) e devolvido logo antes do proximo
    movimento com E da base, onde o fatiador faria a volta da retracao; se vier antes uma troca, ela zera o estado.
    trechos: linhas dos trechos em ordem; entre dois trechos entra a correcao de retracao que o seguinte espera."""
    base, trechos, i, n, pend, saida_ant = [], [], 0, len(src), 0.0, None
    while i < n:
        ln = src[i]
        m = ROT_START.match(ln)
        comeca = (ln.startswith('; OBJECT_ID: ') and int(ln.split(':')[1]) in rotulos and i + 1 < n
                  and ROT_START.match(src[i + 1])) or (m and int(m.group(1)) in rotulos)
        if not comeca:
            if eh_troca(ln): pend = 0.0                  # a troca descarrega e recarrega: o estado volta ao dela
            elif abs(pend) > 1e-3 and MOV.match(ln) and _num(ln, 'E') is not None:
                base.append(f'G1 E{pend:.5f} F1800 {TAG} saldo de retracao do trecho retirado'); pend = 0.0
            base.append(ln); i += 1; continue
        j = i
        while not (ROT_STOP.match(src[j]) and int(ROT_STOP.match(src[j]).group(1)) in rotulos): j += 1
        if j + 1 < n and src[j + 1].startswith('M625'): j += 1
        trecho = [l for l in src[i:j + 1] if not l.startswith('M73')]
        prog = [l for l in src[i:j + 1] if l.startswith('M73')]
        s_in, s_out = _estados(trecho)
        if saida_ant is not None and abs(s_in - saida_ant) > 1e-3:
            trechos.append(f'G1 E{s_in - saida_ant:.5f} F1800 {TAG} retracao que o proximo trecho espera')
        trechos += trecho
        saida_ant = s_out
        pend += s_out - s_in
        base.append(RETIRADO)
        base += prog                                    # progresso da tela continua na ordem do tempo
        for padrao in ('M204', 'M106 S'):
            ult = [l for l in trecho if l.startswith(padrao)]
            if ult: base.append(f'{ult[-1].split(";")[0].strip()} {TAG} estado deixado pelo trecho')
        i = j + 1
    return base, trechos

def fluxo(linhas):
    """Estado apos cada linha dos trechos: posicao, F, volume extrudado, retracao (0 = pronto para extrudar),
    etiqueta aberta (M624). lin[0]['ret_antes'] = retracao esperada antes da 1a linha."""
    out, x, y, z, f = [], None, None, None, None
    acel, extr, s, mask = 6000, 0.0, 0.0, None
    for ln in linhas:
        cod = ln.split(';')[0].strip()
        tipo = 'outro'
        if ln.startswith('M624 '): mask = cod.split()[1]; tipo = 'marca'
        elif ln.startswith('M625'): mask = None; tipo = 'marca'
        elif ln.startswith('M204'): acel = int(_num(ln, 'S') or acel); tipo = 'acel'
        elif FAN.match(ln): tipo = 'fan'
        elif MOV.match(ln):
            X, Y, Z, E, F = (_num(cod, k) for k in 'XYZEF')
            x = X if X is not None else x; y = Y if Y is not None else y; z = Z if Z is not None else z
            f = F if F is not None else f
            tem_xy = X is not None or Y is not None or cod.startswith(('G2', 'G3'))
            ext = E is not None and E > 0 and tem_xy
            if E is not None and not ext: s += E
            if ext: extr += E * A175
            tipo = 'ext' if ext else 'mov'
        out.append({'ln': ln, 'tipo': tipo, 'x': x, 'y': y, 'z': z, 'f': f, 'acel': acel, 'vol': extr, 'ret': s, 'mask': mask})
    ext = [l['ret'] for l in out if l['tipo'] == 'ext']
    off = -ext[0] if ext else 0.0                       # retracao zero = estado em que o fatiador extruda
    for l in out: l['ret'] = round(l['ret'] + off, 4)
    if out: out[0]['ret_antes'] = round(off, 4)
    fora = [l['ret'] for l in out if l['tipo'] == 'ext' and abs(l['ret']) > 0.05]
    if fora: raise ValueError(f'fluxo do objeto incoerente: {len(fora)} extrusoes com retracao pendente (ex. {fora[:3]})')
    return out

def _antes(lin, i0):
    est = next((lin[j] for j in range(i0 - 1, -1, -1) if lin[j]['tipo'] in ('ext', 'mov') and lin[j]['x'] is not None), None)
    acel = next((int(_num(lin[j]['ln'], 'S')) for j in range(i0 - 1, -1, -1) if lin[j]['tipo'] == 'acel'), 6000)
    fan = next((float(FAN.match(lin[j]['ln']).group(1)) for j in range(i0 - 1, -1, -1) if lin[j]['tipo'] == 'fan'), 0.0)
    return est, acel, fan, (lin[i0 - 1]['mask'] if i0 > 0 else None)


def rota_y(y0, jesus):
    """Y da viagem pela borda: fora da faixa Y dos Jesus (com 15 mm), para o trecho em X nao passar sobre eles."""
    a, b = jesus[1] - 15, jesus[3] + 15
    if not (a < y0 < b): return y0
    return b if (b <= 250 and (y0 >= (a + b) / 2 or a < 6)) else a


def bloco(lin, i0, i1, ctx, extra_ret, temp, volta=None):
    est, acel, fan, mask0 = _antes(lin, i0)
    if est is None: est = next(l for l in lin[i0:i1] if l['tipo'] in ('ext', 'mov') and l['x'] is not None)
    x0, y0, z0, f0 = est['x'], est['y'], est['z'], est['f'] or 3000
    if z0 is None: z0 = next(l['z'] for l in lin[i0:i1] if l['z'] is not None)
    zmax_ped = max([l['z'] for l in lin[i0:i1] if l['z'] is not None] + [z0])
    zs = max(ctx['z_atual'], ctx['z_obj_max'] + 1.0, ctx['z_cam'] + 2.0)
    u = RET_TROCA + extra_ret
    s_antes = lin[i0 - 1]['ret'] if i0 > 0 else lin[0].get('ret_antes', 0.0)
    out = [INI,
           f'G1 E{u:g} F1800 {TAG} devolve a retracao da troca',
           f'G1 E{ctx["escorva"]:g} F300 {TAG} escorva',
           f'G1 E-0.8 F1800 {TAG} retracao para a viagem',
           f'G1 Z{zs:.3f} F1200 {TAG} rota', f'G1 Y{rota_y(y0, ctx["jesus"]):.3f} F12000 {TAG} rota',
           f'G1 X{x0:.3f} F30000 {TAG} rota', f'G1 Y{y0:.3f} F12000 {TAG} rota',
           f'G1 Z{z0:.3f} F1200 {TAG} desce', f'M204 S{acel} {TAG}', f'M106 S{fan:g} {TAG}',
           f'G1 E{0.8 + s_antes:.4f} F1800 {TAG} volta da retracao', f'G1 F{f0:.0f} {TAG}']
    if mask0: out.append(f'M624 {mask0} {TAG} reabre a etiqueta do objeto')
    out += [FLX_INI] + [l['ln'] for l in lin[i0:i1]] + [FLX_FIM]
    if lin[i1 - 1]['mask']: out.append(f'M625 {TAG} fecha a etiqueta do objeto')
    ret_fim = lin[i1 - 1]['ret']
    if abs(ret_fim) > 1e-4: out.append(f'G1 E{-ret_fim:.4f} F1800 {TAG} devolve a retracao do fim do trecho')
    out.append(f'G1 E-{u:g} F1800 {TAG} deixa a retracao como a troca deixou')
    novo_max = max(ctx['z_obj_max'], zmax_ped)
    zsai = max(ctx['z_atual'], novo_max + 1.0, ctx['z_cam'] + 2.0)
    out.append(f'G1 Z{zsai:.3f} F1200 {TAG} saida')
    if temp: out.append(f'M109 S{temp} {TAG} espera a temperatura aqui (bico erguido), nao na canaleta')
    out += [f'M204 S{ctx["acel_j"]} {TAG}', f'M106 S{ctx["fan_j"]:g} {TAG}']
    if volta: out.append(f'G1 X{volta[0]:.3f} Y{volta[1]:.3f} F30000 {TAG} volta')
    out.append(FIM)
    return out, novo_max


def caixa_jesus(base):
    xs, ys, x, y, lab = [], [], None, None, None
    for ln in base:
        if ROT_START.match(ln): lab = 1
        elif ROT_STOP.match(ln): lab = None
        if ln[:1] != 'G': continue                    # atalho: so linha de movimento interessa
        c = ln.split(';')[0]
        if not MOV.match(c): continue
        X, Y, E = _num(c, 'X'), _num(c, 'Y'), _num(c, 'E')
        x = X if X is not None else x; y = Y if Y is not None else y
        if lab and E and E > 0 and x is not None: xs.append(x); ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _so_objeto(base):
    """[(indice da linha M620, 'S2A', z)] das trocas (na impressao) depois das quais nao ha extrusao de nenhum outro
    objeto ate a proxima troca."""
    ruins, impressao, aberta, teve, lab, z, dentro_troca = [], False, None, False, None, 0.0, False
    for i, ln in enumerate(base):
        if ln.startswith('; CHANGE_LAYER'): impressao = True
        if ln.startswith('; Z_HEIGHT:'): z = float(ln.split(':')[1])
        if eh_troca(ln) or ln.startswith('; MACHINE_END_GCODE_START'):
            if aberta is not None and not teve: ruins.append(aberta)
            aberta, teve = ((i, ln.split()[1], z) if impressao and eh_troca(ln) else None), False
        if ROT_START.match(ln): lab = 1
        elif ROT_STOP.match(ln): lab = None
        if eh_troca(ln): dentro_troca = True
        elif ln.startswith('M621 S'): dentro_troca = False
        if aberta is not None and not teve and ln[:1] == 'G' and MOV.match(ln):   # atalho: so importa ate achar
            e = _num(ln, 'E')
            c = ln.split(';')[0]
            com_xy = _num(c, 'X') is not None or _num(c, 'Y') is not None
            # outra peca com rotulo, ou extrusao sem rotulo fora da troca (saia, torre de limpeza)
            if e and e > 0 and (lab or (com_xy and not dentro_troca)): teve = True
    return ruins


def trocas_so_do_objeto(base):
    """Trocas (na impressao) depois das quais nao ha extrusao de nenhum outro objeto ate a proxima troca."""
    return [(s, z) for _, s, z in _so_objeto(base)]


# ---- trocas que existem so por causa do objeto: tiradas pelo programa (em vez de recusar) ----
FIL = re.compile(r'M620 S(\d+)A')


def tabela_flush(z):
    """(matriz de purga [filamento antigo][novo] em mm3, multiplicador) do projeto do Studio."""
    d = json.loads(z.read('Metadata/project_settings.config').decode())
    v = [float(x) for x in d['flush_volumes_matrix']]
    n = round(len(v) ** 0.5)
    if n * n != len(v): raise ValueError('matriz de purga do projeto com tamanho estranho')
    for k in ('filament_type', 'nozzle_temperature'):
        if len(set(d.get(k) or [''])) > 1:
            raise ValueError(f'filamentos com {k} diferentes {d.get(k)}: a troca que fica guardaria os dados do filamento '
                             'tirado; troque a cor do objeto no Studio (ou rode com --recusar)')
    return [v[i * n:(i + 1) * n] for i in range(n)], float((d.get('flush_multiplier') or ['1'])[0])


def _purga_bloco(base, k, fim):
    dentro, tot = False, 0.0
    for m in range(k, fim):
        if base[m].startswith('; FLUSH_START'): dentro = True
        elif base[m].startswith('; FLUSH_END'): dentro = False
        elif dentro and base[m].startswith('G1 E'): tot += _num(base[m], 'E') * A175
    return tot


def blocos_troca(base):
    """Cada troca: ini = '; filament end gcode' antes do M620 (None se nao achar), k = M620, fim = M621,
    fim_cauda = fim das linhas que o Studio poe depois do M621, n = filamento novo."""
    out, i = [], 0
    while i < len(base):
        if not eh_troca(base[i]): i += 1; continue
        k, fim = i, i
        while not base[fim].startswith('M621 S'): fim += 1
        ini = next((j for j in range(k - 1, max(k - 16, -1), -1) if base[j].startswith('; filament end gcode')), None)
        m = fim + 1
        while m < len(base) and not (base[m].startswith('; OBJECT_ID') or ROT_START.match(base[m]) or base[m] == RETIRADO
                                     or MOV.match(base[m]) or base[m].startswith('; CHANGE_LAYER')):
            m += 1
        out.append({'ini': ini, 'k': k, 'fim': fim, 'fim_cauda': m, 'n': int(FIL.match(base[k]).group(1))})
        i = m
    return out


def desconto_fixo(base, bl, M, mult):
    """Formula da purga de cada troca, medida no proprio arquivo: purga = a * matriz[antigo][novo] + b (com fator 1 o
    Studio deu a = 1, b = -41,8 mm3; com outro fator de purga a e b mudam). Exige que TODAS as trocas caiam na reta
    (folga 1 mm3); senao a purga nao segue so a matriz e o programa nao sabe refazer a conta. -> (a, b)"""
    xs, ys, ant = [], [], None
    for b in bl:
        if ant is not None and ant != b['n']:
            if max(ant, b['n']) >= len(M): raise ValueError('filamento fora da matriz de purga')
            xs.append(M[ant][b['n']]); ys.append(_purga_bloco(base, b['k'], b['fim']))
        ant = b['n']
    if not xs: raise ValueError('nenhuma troca para medir a formula da purga')
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 1e-9 else mult
    b0 = my - a * mx
    pior = max(abs(a * x + b0 - y) for x, y in zip(xs, ys))
    if pior > 1.0:
        raise ValueError(f'a purga das trocas nao segue a matriz de purga (desvio {pior:.1f} mm3; purga em objetos/enchimento ligada?)')
    return a, b0


def tira_trocas(base, M, mult, ruins=None):
    """Tira as trocas que so existem por causa do objeto de sacrificio (nenhuma outra peca extruda depois delas) e as que
    por isso viram 'troca para o filamento que ja esta no bico'. O objeto passa a ser impresso com a cor que estiver
    carregada. Nas trocas que ficam, a purga e refeita para a cor que de fato esta no bico (matriz*mult - desconto fixo).
    No lugar de cada troca tirada ficam: o ajuste do extrusor (leva o estado ao que a troca deixaria, para os Jesus
    encontrarem o mesmo estado de antes) e a subida em espiral do bico (para a viagem ate a peca nao arrastar nela)."""
    bl = blocos_troca(base)
    ruins = {k for k, _, _ in (_so_objeto(base) if ruins is None else ruins)}
    desc = desconto_fixo(base, bl, M, mult)
    i_imp = next((i for i, l in enumerate(base) if l.startswith('; CHANGE_LAYER')), len(base))
    rem, fator, carr, ant, rel = set(), {}, None, None, {'so_objeto': 0, 'redundantes': 0, 'refeitas': 0, 'desconto': desc}
    for bi, b in enumerate(bl):
        n, imp = b['n'], b['k'] > i_imp
        if imp and (b['k'] in ruins or carr == n):
            rem.add(bi); rel['so_objeto' if b['k'] in ruins else 'redundantes'] += 1
        else:
            if carr is not None and ant is not None and carr != ant:
                if n >= len(M) or carr >= len(M): raise ValueError('filamento fora da matriz de purga')
                orig = _purga_bloco(base, b['k'], b['fim'])
                if orig <= 0: raise ValueError('troca sem purga para refazer')
                fator[bi] = max(0.0, desc[0] * M[carr][n] + desc[1]) / orig
                rel['refeitas'] += 1
            carr = n
        ant = n
    inicio = {}
    for bi, b in enumerate(bl):
        if bi in rem and b['ini'] is None: raise ValueError('troca sem "filament end gcode" antes dela: nao sei onde ela comeca')
        inicio[b['ini'] if bi in rem else b['k']] = bi
    # Copia em bloco o que fica entre as trocas; s (retracao acumulada desde o fim da troca anterior, mesma conta do
    # conferidor) so e somado no trecho antes de cada troca TIRADA, que e onde ele e usado (19/10, velocidade).
    out, pos = [], 0
    for st in sorted(inicio):
        if st < pos: continue                       # comeco engolido pela troca anterior: o laco antigo tambem pulava
        bi = inicio[st]; b = bl[bi]
        trecho = base[pos:st]
        out.extend(trecho)
        if bi in rem:
            s = 0.0
            for l in trecho: s += _e_nao_ext(l)
            lift = [l for l in base[b['ini']:b['k']] if l.startswith(('G17', 'G2 ', 'G3 '))]
            if not any(l.startswith(('G2 ', 'G3 ')) for l in lift): raise ValueError('troca sem subida em espiral para copiar')
            out.append(f'G1 E{-s:.5f} F1800 {TAG} troca retirada: devolve o extrusor ao estado que a troca deixaria')
            out += [f'{l.rstrip()} {TAG} subida da troca retirada' if l.startswith(('G2 ', 'G3 ')) else l for l in lift]
            pos = b['fim_cauda']
            continue
        f = fator.get(bi, 1.0)
        dentro = False
        for m in range(b['k'], b['fim'] + 1):
            l = base[m]
            if l.startswith('; FLUSH_START'): dentro = True
            elif l.startswith('; FLUSH_END'): dentro = False
            elif dentro and l.startswith('G1 E') and f != 1.0:
                l = re.sub(r'E[\d.]+', f'E{_num(l, "E") * f:.5f}', l, count=1)
            elif f != 1.0 and l.startswith('M620.10 A1') and ' L' in l:     # L = comprimento da purga declarado ao firmware
                l = re.sub(r' L([\d.]+)', lambda r: f' L{float(r.group(1)) * f:.3f}', l, count=1)
            out.append(l)
        pos = b['fim'] + 1
    out.extend(base[pos:])
    return out, rel


def ultimo_xy(base, k):
    """ultimo X e ultimo Y de movimento em base[0..k] (o que o acompanhamento linha a linha guardava), 0.0 se nao houver."""
    xj = yj = None
    for i in range(k, -1, -1):
        l = base[i]
        if l[:1] != 'G' or not MOV.match(l): continue
        c = l.split(';')[0]
        if xj is None and 'X' in c:
            x = _num(c, 'X')
            if x is not None: xj = x
        if yj is None and 'Y' in c:
            y = _num(c, 'Y')
            if y is not None: yj = y
        if xj is not None and yj is not None: break
    return (0.0 if xj is None else xj), (0.0 if yj is None else yj)


def reescreve(base, lin, manter=0.0, escorva=1.0, jesus=None):
    total = lin[-1]['vol'] if lin else 0.0
    n_trocas = sum(1 for l in base if eh_troca(l))
    ctx = {'z_atual': 0.0, 'z_obj_max': 0.0, 'z_cam': 0.0, 'acel_j': 6000, 'fan_j': 0.0, 'escorva': escorva,
           'jesus': caixa_jesus(base) if jesus is None else jesus}
    out, i_obj, xj, yj, impressao, vistas, t = [], 0, 0.0, 0.0, False, set(), -1
    rel = {'trocas': 0, 'pedacos': [], 'objeto_mm3': total, 'sobra_mm3': 0.0}

    def acompanha(l):
        c0 = l[:1]                                     # despacho pelo 1o caractere: mesmas regras, menos testes
        if c0 == 'G':
            # so a linha com Z muda z_atual; X e Y do bico so sao usados no fim (ultimo_xy), nao a cada linha (19/10)
            if 'Z' in l and MOV.match(l):
                z = _num(l.split(';')[0], 'Z')
                if z is not None: ctx['z_atual'] = z
        elif c0 == 'M':
            if l.startswith('M204'): ctx['acel_j'] = int(_num(l, 'S') or ctx['acel_j'])
            else:
                f = FAN.match(l)
                if f: ctx['fan_j'] = float(f.group(1))
        elif c0 == ';' and l.startswith('; Z_HEIGHT:'): ctx['z_cam'] = float(l.split(':')[1])

    k = 0
    while k < len(base):
        ln = base[k]
        acompanha(ln)
        if ln.startswith('; CHANGE_LAYER'): impressao = True
        if eh_troca(ln):
            t += 1
            ferr = ln.split()[1]
            fim = k
            while not base[fim].startswith('M621 S'): fim += 1
            linhas_e, dentro, ult_flush = [], False, k
            for m in range(k, fim):
                if base[m].startswith('; FLUSH_START'): dentro = True
                elif base[m].startswith('; FLUSH_END'): dentro = False; ult_flush = m
                elif dentro and base[m].startswith('G1 E'): linhas_e.append(m)
            purga = sum(_num(base[m], 'E') for m in linhas_e) * A175
            ultima = t == n_trocas - 1
            alvo = float('inf') if ultima else max(0.0, purga - manter)
            if impressao and i_obj < len(lin) and alvo > 1:
                i1 = pedaco(lin, i_obj, alvo)
                vol = lin[i1 - 1]['vol'] - _vol_antes(lin, i_obj)
            else:
                i1, vol = i_obj, 0.0
            usar = min(vol, purga)
            fator = (purga - usar) / purga if purga > 0 else 1.0
            espera = None   # a espera de temperatura fica na canaleta (revisao 18/10: move-la tornava o E6 purga pura)
            temp = None
            for m in range(k, fim + 1):
                l = base[m]
                if m in linhas_e:
                    e = _num(l, 'E') * fator
                    if e > 1e-4: out.append(re.sub(r'E[\d.]+', f'E{e:.5f}', l, count=1))
                    continue
                if m == espera:
                    temp = int(_num(l, 'S'))
                    out.append(f'M104 S{temp} {TAG} espera movida para o fim do pedaco'); continue
                if m > k: acompanha(l)
                out.append(l)
            m = fim + 1
            while m < len(base) and not (base[m].startswith('; OBJECT_ID') or ROT_START.match(base[m]) or base[m] == RETIRADO
                                         or MOV.match(base[m]) or base[m].startswith('; CHANGE_LAYER')):
                acompanha(base[m]); out.append(base[m]); m += 1
            extra = 0.0 if (ferr in vistas or not impressao) else RET_TROCA     # 1o uso da cor: calibracao recolhe +2 mm
            vistas.add(ferr)
            if i1 > i_obj:
                txt, ctx['z_obj_max'] = bloco(lin, i_obj, i1, ctx, extra, temp)
                out += txt
            rel['trocas'] += 1
            rel['pedacos'].append({'z_jesus': ctx['z_cam'], 'purga': purga, 'pedaco': vol, 'tirado': usar, 'z_obj_max': ctx['z_obj_max']})
            if ultima and vol > usar: rel['sobra_mm3'] = vol - usar
            i_obj = i1
            k = m
            continue
        if ln.startswith('; MACHINE_END_GCODE_START') and i_obj < len(lin):
            xj, yj = ultimo_xy(base, k)
            txt, ctx['z_obj_max'] = bloco(lin, i_obj, len(lin), ctx, 0.0, None, volta=(xj, yj))
            out += txt
            rel['sobra_mm3'] = total - _vol_antes(lin, i_obj)
            i_obj = len(lin)
        out.append(ln)
        k += 1
    return out, rel
