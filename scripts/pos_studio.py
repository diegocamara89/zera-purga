"""Pos-processamento do Bambu Studio: zera a purga DENTRO do Studio, para o trabalho sair do Studio com a lista de pecas
(so assim o Handy deixa pular objeto: trabalho que passa pela nuvem, enviado pelo Studio a partir do projeto fatiado).

No Studio: Processo > Outros > Scripts de pos-processamento, uma linha com o caminho do zera_purga_studio.bat (veja o
README). O Studio chama com o caminho do G-code temporario no fim. O script reescreve esse arquivo NO LUGAR.

Objeto de sacrificio = nome com 'sacrif' ou 'purga'. O G-code nao tem nomes: eles vem da copia do projeto que o Studio
mantem em %TEMP%\\bamboo_model\\..., aceita so se os rotulos baterem com os do G-code.
Projeto SEM objeto de sacrificio: G-code fica como o Studio fez (impressao comum).
Projeto COM objeto de sacrificio e qualquer falha (pacote sem nomes, erro, reprovacao do conferidor): BLOQUEIA (decisao do
dono, 19/10): o G-code vira um arquivo vazio que nao imprime nada e o script sai com erro, para o Studio avisar. Nunca
imprime purga normal num projeto feito para zerar. Motivo no pos_studio.log ao lado deste script."""
import glob, json, os, re, sys, time, traceback, zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
LOG = os.path.join(AQUI, 'pos_studio.log')
T0 = time.time()
PREVISTO = {1: 1, 2: 1, 3: 5, 4: 9, 5: 18, 6: 19}     # segundos ate o fim de cada etapa (medido 19/10, passo 3: 18 Marias + 2 objetos)


def log(msg):
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(time.strftime('%d/%m %H:%M:%S ') + msg + '\n')


import threading
os.system('')                      # liga as cores ANSI no console do Windows
COR = {'v': '\033[92m', 'a': '\033[93m', 'c': '\033[96m', 'r': '\033[91m', 'n': '\033[1m', 'x': '\033[0m'}
_atual = {'n': 0, 't': T0, 'msg': ''}
_para = threading.Event()


def _mmss(s):
    return f'{int(s // 60)} min {int(s % 60):02d} s' if s >= 60 else f'{int(s)} s'


def _barra():
    """redesenha a linha da etapa em andamento a cada segundo: barra, %, tempo gasto e o que falta (estimado)."""
    while not _para.wait(1.0):
        n = _atual['n']
        if not n: continue
        gasto = time.time() - T0
        ini, fim = PREVISTO.get(n - 1, 0), PREVISTO[n]
        dentro = min(0.97, (gasto - ini) / max(1, fim - ini)) if gasto > ini else 0.0
        pct = min(99, int(100 * (ini + dentro * (fim - ini)) / PREVISTO[6]))
        cheio = pct * 30 // 100
        falta = max(5, PREVISTO[6] - gasto) if gasto < PREVISTO[6] else None
        resto = f'faltam ~{_mmss(falta)}' if falta else 'quase la (placa maior que o previsto)'
        gira = '|/-\\'[int(gasto) % 4]
        sys.stdout.write(f"\r  {COR['c']}{gira}{COR['x']} [{COR['v']}{'█' * cheio}{COR['x']}{'░' * (30 - cheio)}] "
                         f"{pct:3d}%  {_mmss(gasto):>12}  {resto:<40}")
        sys.stdout.flush()


def etapa(n, msg, feito=None):
    """fecha a etapa anterior (com o detalhe dela) e abre a nova; a barra (_barra) mostra o andamento entre uma e outra."""
    agora = time.time()
    if _atual['n']:
        sys.stdout.write('\r' + ' ' * 100 + '\r')
        print(f"  {COR['v']}✔{COR['x']} {_atual['msg']}  {COR['a']}({_mmss(agora - _atual['t'])}){COR['x']}"
              + (f"\n      {feito}" if feito else ''), flush=True)
    _atual.update(n=n, t=agora, msg=msg)
    if n:
        print(f"{COR['n']}[{n}/6]{COR['x']} {msg}", flush=True)


def config_do_gcode(linhas):
    cfg, dentro = {}, False
    for l in linhas[:20000]:
        if l.startswith('; CONFIG_BLOCK_START'): dentro = True; continue
        if l.startswith('; CONFIG_BLOCK_END'): break
        if dentro and ' = ' in l:
            k, v = l[2:].split(' = ', 1); cfg[k.strip()] = v.strip()
    return cfg


def tabela(cfg):
    v = [float(x) for x in cfg['flush_volumes_matrix'].split(',')]
    n = round(len(v) ** 0.5)
    if n * n != len(v): raise ValueError('matriz de purga com tamanho estranho')
    for k in ('filament_type', 'nozzle_temperature'):
        if len(set(re.split('[;,]', cfg.get(k, '')))) > 1:
            raise ValueError(f'filamentos com {k} diferentes: nao refaco a purga')
    return [v[i * n:(i + 1) * n] for i in range(n)], float(cfg.get('flush_multiplier', '1').split(',')[0])


def nomes_do_studio(ids, espera=20):
    """{label: nome} do pacote de configuracao mais novo do Studio cuja lista de rotulos = ids do G-code."""
    raiz = os.path.join(os.environ.get('TEMP', ''), 'bamboo_model')
    fim = time.time() + espera
    while True:
        cands = sorted(glob.glob(os.path.join(raiz, '**', '.*_config.3mf'), recursive=True), key=os.path.getmtime, reverse=True)[:6]
        for c in cands:
            try:
                si = zipfile.ZipFile(c).read('Metadata/slice_info.config').decode()
                nm = dict(re.findall(r'identify_id="(\d+)" name="([^"]+)"', si))
                if set(map(int, nm)) == set(ids): return nm, c
            except Exception:
                pass
        if time.time() > fim: return None, None
        time.sleep(2)


def sacrificio_pelo_backup(caminho, ids):
    """Rotulos de sacrificio pela copia viva do projeto que o Studio grava ao fatiar (<pasta temporaria>/.3mf):
    model_settings tem o nome de cada objeto e o identify_id (= rotulo do G-code) de cada instancia. Vale mesmo com o
    projeto NAO salvo. -> (rotulos, fonte) ou (None, motivo)."""
    vivo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(caminho))), '.3mf')
    if not os.path.exists(vivo): return None, f'sem {vivo}'
    ms = zipfile.ZipFile(vivo).read('Metadata/model_settings.config').decode('utf-8', 'ignore')
    nomes = dict(re.findall(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"', ms))
    inst = re.findall(r'key="object_id" value="(\d+)"/>\s*<metadata key="instance_id" value="\d+"/>\s*'
                      r'<metadata key="identify_id" value="(\d+)"', ms)
    rotulo_nome = {int(i): nomes.get(o, '') for o, i in inst}
    if not set(ids) <= set(rotulo_nome):
        return None, f'copia viva sem todos os rotulos do G-code ({len(set(ids) & set(rotulo_nome))} de {len(ids)})'
    return [k for k in ids if re.search(r'sacrif|purga', rotulo_nome[k], re.I)], vivo


def sacrificio_pelo_projeto(caminho, orig, ids):
    """Rotulos dos objetos de sacrificio, casando o projeto aberto no Studio (origin.txt da pasta temporaria, dois
    niveis acima do G-code) com o G-code pela posicao da 1a camada. -> (rotulos, projeto) ou (None, motivo)."""
    import numpy as np
    from le3mf import le_itens
    import posiciona_sacrificio as PS
    origem = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(caminho))), 'origin.txt')
    if not os.path.exists(origem): return None, f'sem {origem}'
    proj = open(origem, encoding='utf-8', errors='ignore').read().strip()
    if not os.path.exists(proj): return None, f'projeto do origin.txt nao existe: {proj}'
    z = zipfile.ZipFile(proj); nm = PS.nomes(z)
    itens = le_itens(proj)
    sac = [it for it in itens if re.search(r'sacrif|purga', nm.get(it['id'], ''), re.I)]
    if not sac: return [], proj
    # 1a camada de cada rotulo no G-code
    cx, lab, x, y, cam = {}, None, None, None, 0
    for l in orig:
        if l.startswith('; CHANGE_LAYER'):
            cam += 1
            if cam > 1: break
        m = re.match(r'; start printing object, unique label id: (\d+)', l)
        if m: lab = int(m.group(1)); continue
        if l.startswith('; stop printing object'): lab = None; continue
        if l.startswith(('G1 ', 'G0 ')):
            c = l.split(';')[0]
            X, Y, E = re.search(r'X([-\d.]+)', c), re.search(r'Y([-\d.]+)', c), re.search(r'E([-\d.]+)', c)
            if X: x = float(X.group(1))
            if Y: y = float(Y.group(1))
            if cam == 1 and lab and E and float(E.group(1)) > 0 and x is not None:
                b = cx.setdefault(lab, [1e9, 1e9, -1e9, -1e9]); b[0] = min(b[0], x); b[1] = min(b[1], y); b[2] = max(b[2], x); b[3] = max(b[3], y)
    if set(cx) != set(ids): return None, f'1a camada do G-code nao tem todos os rotulos ({len(cx)} de {len(ids)})'
    # altura de cada rotulo: cabecalho '; object max height:' na mesma ordem de '; model label id:'
    m = re.search(r'^; object max height: ([\d.,]+)', '\n'.join(orig[:200]), re.M)
    alt = dict(zip(ids, map(float, m.group(1).split(',')))) if m else {}
    rot = []
    for it in sac:
        h = float(it['v'][:, 2].max() - it['v'][:, 2].min())
        v = it['v'][it['v'][:, 2] < it['v'][:, 2].min() + 0.3]
        c0 = ((v[:, 0].min() + v[:, 0].max()) / 2, (v[:, 1].min() + v[:, 1].max()) / 2)
        # candidatos: mesma altura (+-0,6 mm; a borda e o suporte nao mudam a altura); depois o centro mais proximo
        cand = [k for k in cx if k not in rot and (not alt or abs(alt.get(k, -99) - h) < 0.6)]
        d = sorted((abs((cx[k][0] + cx[k][2]) / 2 - c0[0]) + abs((cx[k][1] + cx[k][3]) / 2 - c0[1]), k) for k in cand)
        # a borda da 1a camada desloca o centro alguns mm; vale se o 2o candidato estiver bem mais longe
        if not d:
            # objeto do projeto salvo que nao esta na placa fatiada (tirado sem salvar): ignora
            log(f'objeto "{nm.get(it["id"])}" (altura {h:.1f}) do projeto salvo nao esta no G-code: ignorado')
            continue
        if d[0][0] > 15 or (len(d) > 1 and d[1][0] < 3 * max(d[0][0], 3)):
            return None, (f'objeto "{nm.get(it["id"])}" (altura {h:.1f}) nao casa com um unico rotulo '
                          f'(candidatos {[(k, round(dd, 1)) for dd, k in d[:3]]}); salve o projeto (Ctrl+S) e fatie de novo')
        rot.append(d[0][1])
    if not rot:
        return None, 'nenhum objeto de sacrificio do projeto salvo esta no G-code; salve o projeto (Ctrl+S) e fatie de novo'
    return rot, proj


SEG_POR_TROCA = 60.0    # descarga, carga e limpeza de uma troca na A1 (tempo de "Flush" do Studio / trocas, 18/10)
VAZAO_PURGA = 16.0      # mm3/s da purga na canaleta (F399 de filamento 1,75)
SEG_POR_PEDACO = 8.0    # ida e volta do bico ao objeto de sacrificio em cada troca (estimado)


def resumo(orig, novo, r, rel, K, t_antes=None, t_depois=None):
    """antes x depois na janela: purga, trocas e tempo (o novo vem de tempo.refaz_m73)."""
    import purga3 as P
    trocas_antes = sum(1 for l in orig if P.eh_troca(l))
    trocas_depois = sum(1 for l in novo if P.eh_troca(l))
    purga_antes = sum(K.normaliza(orig)[2]) * 1.27e-3
    purga_depois = r['canaleta_depois_g'] + r['escorva_g']
    hm = lambda s: f'{int(s // 3600)} h {int(s % 3600 // 60):02d} min'
    v, a, n, x, c = COR['v'], COR['a'], COR['n'], COR['x'], COR['c']
    linhas = ['', c + '=' * 64 + x, f'{n}  ANTES x DEPOIS DO ZERA PURGA{x}', c + '=' * 64 + x,
              f'  Purga jogada fora : {purga_antes:6.1f} g  ->  {v}{n}{purga_depois:5.1f} g{x}   (vai para o objeto: {r["absorvido_g"]:.1f} g)',
              f'  Trocas de cor     : {trocas_antes:6d}    ->  {v}{n}{trocas_depois:5d}{x}']
    if t_antes and t_depois:
        linhas.append(f'  Tempo de impressao: {hm(t_antes)}  ->  {v}{n}{hm(t_depois)}{x}   ({a}-{hm(t_antes - t_depois)}{x})')
        linhas.append('  A impressora mostra o tempo novo. O Studio continua mostrando o antigo (ele calcula antes do script).')
    linhas += [c + '=' * 64 + x, '']
    print(chr(10).join(linhas), flush=True)
    log(re.sub(chr(27) + r'\[[0-9;]*m', '', ' | '.join(l.strip() for l in linhas if l.strip() and '=' * 10 not in l)))
    espera_tecla('Aperte qualquer tecla para fechar esta janela e o Studio continuar.')


def espera_tecla(msg):
    if os.environ.get('ZERA_SEM_TECLA'): return          # testes automaticos
    print(f"{COR['a']}{msg}{COR['x']}", flush=True)
    try:
        import msvcrt
        msvcrt.getch()
    except Exception:
        try: input()
        except Exception: pass


def bloqueia(caminho, motivo):
    _para.set()
    log('BLOQUEADO: ' + motivo)
    nl = chr(10)
    vazio = ('; ZERA_PURGA BLOQUEADO: ' + motivo.replace(nl, ' ')[:300] + nl +
             '; veja ' + LOG.replace(os.sep, '/') + nl + 'M400' + nl)
    with open(caminho, 'w', encoding='utf-8', newline=nl) as f: f.write(vazio)
    sys.stderr.write('ZERA_PURGA BLOQUEADO: ' + motivo + nl)
    print(nl + COR['r'] + '*** BLOQUEADO: nada vai imprimir. Motivo: ' + motivo + COR['x'], flush=True)
    espera_tecla('Aperte qualquer tecla para fechar.')
    sys.exit(1)


def cabecalho():
    print(f"{COR['c']}{'=' * 64}{COR['x']}")
    print(f"{COR['n']}  ZERA PURGA{COR['x']}  - a purga das trocas de cor vai para os objetos de sacrificio")
    print("  Nao feche esta janela: o Studio continua sozinho quando ela fechar.")
    print(f"{COR['c']}{'=' * 64}{COR['x']}", flush=True)


def checklist(r):
    """o que o conferidor provou, em linhas curtas (verde = ok)."""
    import confere3 as K
    itens = [('Outras pecas com o mesmo filamento em cada linha', r['filamento_jesus_igual']),
             ('Outras pecas com o mesmo estado do extrusor', r['estado_jesus_igual']),
             ('Objeto de sacrificio impresso inteiro', r['objeto_completo']),
             ('Purga de cada troca = matriz do projeto', r['pior_dif_matriz_mm3'] < 0.5 and r['pior_dif_troca_mm3'] < 2),
             (f"Distancia do objeto as pecas {r['dist_obj_jesus_mm']:.0f} mm (min {K.FOLGA_CABECA:.0f})",
              r['dist_obj_jesus_mm'] >= K.FOLGA_CABECA and (r['dz_max_mm'] < K.BARRA_Z or r['folga_y_barra_mm'] >= K.BARRA_Y + K.BARRA_MARGEM)),
             ('Bico nunca passa baixo sobre as pecas', not (r['viagens_baixas_sobre_jesus'] or r['rota_abaixo_do_seguro']))]
    return '\n      '.join(f"{COR['v'] + '✔' if ok else COR['r'] + '✘'}{COR['x']} {txt}" for txt, ok in itens)


def main():
    import concurrent.futures as CF, shutil, tempfile
    import paralelo as W
    caminho = sys.argv[-1]
    log(f'chamado: {sys.argv[1:]}')
    cabecalho()
    threading.Thread(target=_barra, daemon=True).start()
    etapa(1, 'Lendo o G-code que o Studio gerou')
    txt = open(caminho, encoding='utf-8', errors='ignore').read()
    orig = txt.split('\n')
    m = re.search(r'^; model label id: ([\d,]+)', txt[:200000], re.M)
    if not m:
        etapa(0, ''); log('sem "; model label id" (1 objeto ou mais de 64): nada feito'); return
    ids = [int(x) for x in m.group(1).split(',')]
    pool = CF.ProcessPoolExecutor(max_workers=int(os.environ.get('ZERA_PROCESSOS') or max(2, min(14, (os.cpu_count() or 4) - 2))))
    f_tempo = pool.submit(W.w_tempo_orig, caminho)            # so depende do original: ja comeca
    etapa(2, 'Achando os objetos de sacrificio no projeto aberto',
          f'{len(orig) / 1e6:.1f} milhoes de linhas, {len(ids)} objetos na placa')
    rot, fonte = sacrificio_pelo_backup(caminho, ids)
    if rot is None:
        log(f'copia viva nao serviu ({fonte}); tentando o projeto salvo')
        try:
            import numpy  # noqa: F401  (o caminho do projeto salvo le a malha com numpy)
        except ImportError:
            if sys.implementation.name != 'pypy':
                pool.shutdown(cancel_futures=True)
                bloqueia(caminho, f'copia viva nao serviu ({fonte}) e falta o numpy para ler o projeto salvo: '
                                  'rode "python -m pip install numpy" e fatie de novo')
            # PyPy nao tem numpy: sai com codigo 3 SEM mexer no G-code; o .bat repete com o Python normal
            pool.shutdown(cancel_futures=True)
            log('sem numpy neste Python (PyPy): pedindo ao .bat para repetir com o Python normal')
            print('Este caso precisa do Python normal: repetindo...', flush=True)
            _para.set()
            sys.exit(3)
        rot, fonte = sacrificio_pelo_projeto(caminho, orig, ids)
    if rot is None:
        nm, f2 = nomes_do_studio(ids, espera=2)
        if nm is None:
            bloqueia(caminho, f'nao achei os objetos de sacrificio: {fonte}')
        rot, fonte = [int(k) for k, n in nm.items() if re.search(r'sacrif|purga', n, re.I)], f2
    if not rot:
        pool.shutdown(cancel_futures=True); etapa(0, ''); log(f'nenhum objeto de sacrificio em {fonte}: nada feito'); return
    log(f'objetos de sacrificio {rot} (de {fonte})')
    f_jo = pool.submit(W.w_jesus, caminho, rot)                # lado do original da conferencia: ja comeca
    etapa(3, 'Separando o objeto e tirando as trocas que eram so dele',
          f'{len(rot)} objeto(s) de sacrificio, numeros {rot}')
    import purga3 as P, confere3 as K, tempo as TP
    base, trechos = P.extrai(orig, rot)
    base_orig = base
    pasta = tempfile.mkdtemp(prefix='zera_purga_')
    p0 = os.path.join(pasta, 'base0.gcode'); W.grava(p0, base_orig)
    f_cx0 = pool.submit(W.w_caixas, p0)            # as duas caixas, adiantadas; conferidas no fim na base final
    so = P._so_objeto(base)                       # calculado uma vez e reaproveitado (19/10, velocidade)
    tab, det3 = None, 'nenhuma troca so do objeto'
    if so:
        tab = tabela(config_do_gcode(orig))
        base, rt = P.tira_trocas(base, *tab, ruins=so)
        det3 = (f"trocas tiradas: {rt['so_objeto']} so do objeto + {rt['redundantes']} que voltavam a mesma cor; "
                f"purga refeita em {rt['refeitas']}")
        log(det3)
    etapa(4, 'Reencaixando o objeto depois de cada troca (a purga vai para ele)', det3)
    lin = P.fluxo(trechos)
    cj, bj = f_cx0.result()
    novo, rel = P.reescreve(base, lin, 0.0, 1.0, jesus=cj)
    pedacos = sum(1 for x in rel['pedacos'] if x['pedaco'] > 0)
    etapa(5, 'Conferindo linha a linha as outras pecas (varias conferencias ao mesmo tempo)',
          f"{rel['trocas']} trocas; o objeto e impresso em {pedacos} pedacos")
    try:
        pb, pn, pt = (os.path.join(pasta, n) for n in ('base.gcode', 'novo.gcode', 'trechos.gcode'))
        W.grava(pb, base); W.grava(pn, novo); W.grava(pt, trechos)
        f_jn = pool.submit(W.w_jesus, pn, rot)
        f_pb = pool.submit(W.w_purga_base, pb)
        f_pnv = pool.submit(W.w_purga_novo, pn, 1.0)
        f_ge = pool.submit(W.w_geometria, pb, pt, pn, bj)
        f_cxf = pool.submit(W.w_caixas, pb)                                            # caixas na base final
        prep = f_tempo.result()
        f_m73 = pool.submit(W.w_m73, pn, prep) if prep[0] else None
        if f_cxf.result() != (cj, bj):
            # a caixa adiantada nao bateu com a da base final: refaz tudo do jeito antigo (sequencial, exato)
            log(f'caixas adiantadas diferentes ({(cj, bj)} x {f_cxf.result()}): refazendo em sequencia')
            novo, rel = P.reescreve(base, lin, 0.0, 1.0)
            r = K.confere(orig, rot, novo, 1.0, tab, pronto=(base_orig, trechos, base))
            f_m73 = pool.submit(W.w_m73, (W.grava(pn, novo), pn)[1], prep) if prep[0] else None
        else:
            purga = K.junta_purga(f_pb.result(), f_pnv.result(), trechos, 1.0, tab, K.desconto(base_orig, tab))
            jo, jn = f_jo.result(), f_jn.result()
            r = K.junta(jo[0], jn[0], jo[1], jn[1], purga, f_ge.result())
        log(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}, ensure_ascii=False))
        etapa(6, 'Refazendo o tempo que a impressora vai mostrar (M73)', checklist(r))
        if not r['ok']:
            bloqueia(caminho, 'REPROVADO pelo conferidor (item com X acima)')
        t_antes = t_depois = None
        try:
            if f_m73:
                trocas_m73, t_depois, info = f_m73.result()
                novo = TP.aplica_m73(novo, trocas_m73)
                t_antes = prep[0]
                log(f"tempo: Studio {t_antes / 3600:.2f} h -> {t_depois / 3600:.2f} h ({info})")
        except Exception as e:
            t_antes = t_depois = None
            log(f'tempo nao refeito (impressora mostra o do Studio): {e}')
    finally:
        pool.shutdown(cancel_futures=True)
        shutil.rmtree(pasta, ignore_errors=True)
    etapa(0, '', 'tempo novo gravado nas linhas M73' if t_depois else 'tempo NAO refeito: a impressora mostra o do Studio')
    tmp = caminho + '.zerando'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f: f.write('\n'.join(novo))
    os.replace(tmp, caminho)
    _para.set()
    print(f"\n  {COR['v']}{COR['n']}PRONTO em {_mmss(time.time() - T0)}{COR['x']}", flush=True)
    resumo(orig, novo, r, rel, K, t_antes, t_depois)
    log(f"OK: purga {r['canaleta_antes_g']:.1f} g -> canaleta {r['canaleta_depois_g']:.1f} g; objetos de sacrificio {rot} absorvem {r['absorvido_g']:.1f} g")


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        bloqueia(sys.argv[-1], 'ERRO no script: ' + traceback.format_exc())
