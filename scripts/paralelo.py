"""Tarefas que o pos_studio roda em processos separados (19/10: 28 nucleos; cada tarefa le o G-code do disco sozinha).
As funcoes de conferencia sao as MESMAS do confere3 (parte_purga, parte_geometria, estado_jesus...); aqui so ha a
leitura do arquivo e a chamada. Resultado identico ao modo sequencial (conferido por hash em 3 placas)."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _mede(f):
    """com ZERA_MEDIR=1, anota em tarefas.log quanto cada tarefa levou (para achar a mais lenta)."""
    import functools, time

    @functools.wraps(f)
    def g(*a, **k):
        t0 = time.time()
        r = f(*a, **k)
        if os.environ.get('ZERA_MEDIR'):
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tarefas.log'), 'a', encoding='utf-8') as fh:
                fh.write('%s %s %.1f s (inicio %.1f)\n' % (time.strftime('%H:%M:%S'), f.__name__, time.time() - t0, t0 % 1000))
        return r
    return g


def le(p):
    with open(p, encoding='utf-8', errors='ignore') as f:
        return f.read().split('\n')


def grava(p, linhas):
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(linhas))


@_mede
def w_estado(p, rot):
    import confere3 as K
    return K.resumo_lista(K.estado_jesus(le(p), rot))


@_mede
def w_filamentos(p, rot):
    import confere3 as K
    return K.resumo_lista(K.filamentos_jesus(le(p), rot))


@_mede
def w_tempo_orig(p):
    import tempo, purga3 as P
    return tempo.prepara_orig(le(p), P.eh_troca)


@_mede
def w_purga(base_p, novo_p, trechos_p, escorva, tab, desc):
    import confere3 as K
    return K.parte_purga(le(base_p), le(novo_p), le(trechos_p), escorva, tab, desc)


@_mede
def w_geometria(base_p, trechos_p, novo_p, bj=None):
    import confere3 as K
    return K.parte_geometria(le(base_p) if bj is None else None, le(trechos_p), le(novo_p), bj)


@_mede
def w_m73(novo_p, prep):
    import tempo, purga3 as P
    return tempo.m73_novo(le(novo_p), P.eh_troca, prep)


@_mede
def w_purga_base(base_p):
    import confere3 as K
    return K.parte_purga_base(le(base_p))


@_mede
def w_purga_novo(novo_p, escorva):
    import confere3 as K
    return K.parte_purga_novo(le(novo_p), escorva)


@_mede
def w_caixa_jesus(base_p):
    import purga3 as P
    return P.caixa_jesus(le(base_p))


@_mede
def w_caixa(base_p):
    import confere3 as K
    return K.caixa(le(base_p))


@_mede
def w_jesus(p, rot):
    import confere3 as K
    oe, of = K.jesus_listas(le(p), rot)
    return K.resumo_lista(oe), K.resumo_lista(of)


@_mede
def w_caixas(base_p):
    import confere3 as K
    return K.caixas_ambas(le(base_p))
