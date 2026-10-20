"""Zera a purga de uma placa fatiada no Studio com um objeto de sacrificio dentro (v3, placa unica).
uso: zera_purga.py <placa.gcode.3mf> [--rotulos auto|900,901] [--manter 0] [--escorva 1.0] [--saida X.gcode.3mf] [--recusar]
--rotulos auto: objetos cujo nome tem 'sacrif' ou 'purga'. Se o Studio criou trocas so por causa da cor do objeto de
sacrificio, o programa tira essas trocas e imprime o objeto com a cor que estiver carregada (ele sai listrado);
com --recusar ele recusa em vez de tirar.
So grava o pacote se o conferidor aprovar; senao grava <saida>_REPROVADO.txt com o motivo."""
import hashlib, json, os, re, sys, zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import purga3 as P, confere3 as K


def rotulos_auto(z):
    si = z.read('Metadata/slice_info.config').decode()
    return [int(i) for i, n in re.findall(r'identify_id="(\d+)" name="([^"]+)"', si) if re.search(r'sacrif|purga', n, re.I)]


def motivos(r):
    """o que reprovou, em palavras, com o que fazer."""
    out = []
    geo = r['dist_obj_jesus_mm'] < K.FOLGA_CABECA or (r['dz_max_mm'] >= K.BARRA_Z and r['folga_y_barra_mm'] < K.BARRA_Y + K.BARRA_MARGEM)
    if geo:
        out.append(f"objeto de sacrificio perto demais das outras pecas (folga em Y {r['folga_y_barra_mm']:.1f} mm, precisa >= "
                   f"{K.BARRA_Y + K.BARRA_MARGEM:.1f}; a cabeca ou a barra X bateria). Rode no PROJETO .3mf: "
                   f"python -I \"{os.path.join(AQUI, 'posiciona_sacrificio.py')}\" \"<projeto>.3mf\" --escala auto ; depois fatie e exporte de novo.")
    if r['viagens_baixas_sobre_jesus'] or r['rota_abaixo_do_seguro'] or r['sobra_sem_volta']:
        out.append('viagem do bico baixa demais sobre as pecas: nao imprima; mande o _REPROVADO.txt para analise.')
    internos = [k for k in ('estado_jesus_igual', 'resto_identico', 'objeto_completo', 'filamento_jesus_igual') if not r[k]]
    if internos or r['trocas_so_do_objeto'] or r['trocas_para_o_mesmo_filamento'] or r['pior_dif_matriz_mm3'] >= 0.5 \
            or r['pior_dif_troca_mm3'] >= 2 or r['blocos_fora_do_lugar'] or r['pior_balanco_e_mm'] >= 0.01:
        out.append(f'falha interna da reescrita ({internos or "contas de purga/extrusor"}): nao imprima; mande o _REPROVADO.txt para analise.')
    return out or ['motivo nao classificado: veja o _REPROVADO.txt']


def main():
    a = sys.argv[1:]
    opt = {'--rotulos': 'auto', '--manter': '0', '--escorva': '1.0', '--saida': None}
    for k in list(opt):
        if k in a: i = a.index(k); opt[k] = a[i + 1]; del a[i:i + 2]
    recusar = '--recusar' in a
    if recusar: a.remove('--recusar')
    if not a: sys.exit(__doc__)
    ent = os.path.abspath(a[0])
    if not os.path.exists(ent):
        pasta = os.path.dirname(ent)
        cand = sorted(f for f in os.listdir(pasta) if f.endswith('.gcode.3mf')) if os.path.isdir(pasta) else []
        sys.exit(f'ARQUIVO NAO ENCONTRADO: {ent}\n  Fatiados nessa pasta: {cand or "nenhum"}. '
                 'Passe o caminho completo do .gcode.3mf exportado pelo Studio (Arquivo > Exportar > Exportar arquivo fatiado da placa).')
    saida = opt['--saida'] or ent.replace('.gcode.3mf', '_zerado.gcode.3mf')
    z = zipfile.ZipFile(ent)
    if 'Metadata/plate_1.gcode' not in z.namelist():
        sys.exit('ESSE ARQUIVO NAO ESTA FATIADO (e o projeto .3mf). No Studio: fatie e use "Exportar arquivo fatiado da placa" '
                 '(.gcode.3mf); rode este programa nele.')
    rot = rotulos_auto(z) if opt['--rotulos'] == 'auto' else [int(x) for x in opt['--rotulos'].split(',')]
    if not rot:
        si = z.read('Metadata/slice_info.config').decode()
        nomes = sorted(set(re.findall(r'identify_id="\d+" name="([^"]+)"', si)))
        sys.exit(f"NENHUM OBJETO DE SACRIFICIO. Objetos da placa: {nomes}. No Studio, renomeie o objeto que vai receber a purga "
                 "para um nome com 'sacrificio' ou 'purga', fatie e exporte de novo.")
    orig = z.read('Metadata/plate_1.gcode').decode('utf-8', 'ignore').split('\n')
    base, trechos = P.extrai(orig, rot)
    base_orig = base
    so = P._so_objeto(base)                        # calculado uma vez e reaproveitado (19/10)
    so_obj = [(s, zz) for _, s, zz in so]
    tab = None
    if so_obj:
        if recusar:
            sys.exit(f'{len(so_obj)} troca(s) existem so por causa do objeto de sacrificio (ex.: {so_obj[:3]}). '
                     'Troque a cor dele no Studio por uma que as outras pecas usam em toda camada em que ele existe.')
        try:
            tab = P.tabela_flush(z)
            base, rt = P.tira_trocas(base, *tab, ruins=so)
        except ValueError as e:
            sys.exit(f'NAO CONSEGUI TIRAR AS TROCAS SO DO OBJETO: {e}')
        print(f"trocas tiradas: {rt['so_objeto']} so do objeto + {rt['redundantes']} que voltariam ao filamento ja carregado; "
              f"purga refeita em {rt['refeitas']} trocas")
    lin = P.fluxo(trechos)
    novo, rel = P.reescreve(base, lin, float(opt['--manter']), float(opt['--escorva']))
    r = K.confere(orig, rot, novo, float(opt['--escorva']), tab, pronto=(base_orig, trechos, base))
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}, ensure_ascii=False))
    if not r['ok']:
        open(saida.replace('.gcode.3mf', '_REPROVADO.txt'), 'w', encoding='utf-8').write(json.dumps(r, indent=1, ensure_ascii=False))
        sys.exit('REPROVADO pelo conferidor: nada foi gravado para imprimir.\n' + '\n'.join('  - ' + m for m in motivos(r)))
    # tempo real para a impressora (linhas M73), como no pos_studio; se nao der, fica o tempo do Studio
    t_antes = t_depois = None
    try:
        import tempo as TP
        novo, t_antes, t_depois, _ = TP.refaz_m73(orig, novo, P.eh_troca)
    except Exception as e:
        print(f'tempo nao refeito (a impressora mostra o do Studio): {e}')
    g = '\n'.join(novo).encode('utf-8')
    zo = zipfile.ZipFile(saida, 'w', zipfile.ZIP_DEFLATED)
    for it in z.infolist():
        d = z.read(it.filename)
        if it.filename == 'Metadata/plate_1.gcode': d = g
        elif it.filename == 'Metadata/plate_1.gcode.md5': d = hashlib.md5(g).hexdigest().upper().encode()
        zo.writestr(it, d)
    zo.close()
    print(f"OK: {saida}\n  purga {r['canaleta_antes_g']:.2f} g -> canaleta {r['canaleta_depois_g']:.2f} g + escorva {r['escorva_g']:.2f} g + compensacao fixa da A1 {r['compensacao_fixa_g']:.2f} g; "
          f"objeto {r['objeto_g']:.2f} g (sobra apos a ultima troca {r['sobra_fim_g']:.2f} g); {rel['trocas']} trocas")
    if t_antes and t_depois:
        hm = lambda s: f'{int(s // 3600)} h {int(s % 3600 // 60):02d} min'
        print(f'  tempo de impressao: {hm(t_antes)} -> {hm(t_depois)} (a impressora mostra o novo)')


if __name__ == '__main__':
    main()
