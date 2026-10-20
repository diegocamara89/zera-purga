# zera-purga

Na troca de cor, a impressora Bambu Lab joga fora filamento na canaleta (a "purga"). O **zera-purga** manda essa purga
para um objeto útil da própria placa, o **objeto de sacrifício**, que você imprime de propósito (uma prateleira, um
vaso, um porta-trecos). As outras peças saem iguais, e todos os objetos continuam podendo ser pulados durante a
impressão.

Ele roda dentro do Bambu Studio, como pós-processamento: você fatia e envia como sempre.

Exemplo real (18 bonecos coloridos + 2 objetos de sacrifício, Bambu A1):

| | Antes | Depois |
|---|---|---|
| Purga jogada fora | 118 g | 18,7 g |
| Trocas de cor | 331 | 280 |
| Tempo estimado | 19 h 10 min | 15 h 36 min |

## Requisitos

- Windows e Bambu Studio.
- Impressora testada: **Bambu Lab A1** com troca de cor (AMS lite ou BMCU). As distâncias de segurança do script
  são as da cabeça da A1; em outra impressora, teste antes com cuidado.
- **Python 3.9 ou mais novo** ([python.org](https://www.python.org/downloads/)). Na instalação, marque
  **"Add python.exe to PATH"**.
- **numpy**: depois de instalar o Python, abra o Prompt de Comando e rode:

  ```
  python -m pip install numpy
  ```

- Opcional, para ficar mais rápido: **PyPy 3** ([pypy.org](https://pypy.org/download.html)). Descompacte em
  `%LOCALAPPDATA%\pypy\` (ex.: `C:\Users\<você>\AppData\Local\pypy\pypy3.11-v7.3.23-win64\`). O atalho acha sozinho.
  Na placa do exemplo, o script leva cerca de 20 s com PyPy.

## Instalação

1. Baixe este repositório (botão **Code > Download ZIP**) e descompacte.
2. Coloque a pasta num caminho **sem espaços**, por exemplo `C:\zera-purga`. Com espaço no caminho, o Studio não
   consegue chamar o script.
3. No Bambu Studio, abra o projeto e vá em **Processo > Outros > Scripts de pós-processamento**. Se a opção não
   aparecer, ligue o modo **Avançado** do processo.
4. Cole o caminho completo do atalho, por exemplo:

   ```
   C:\zera-purga\zera_purga_studio.bat
   ```

5. Salve o projeto (ou salve o perfil de processo, para valer em todos os projetos).

## Como usar

1. **Monte a placa** com as suas peças e pelo menos um objeto de sacrifício.
2. **Dê nome ao objeto de sacrifício** com a palavra `purga` ou `sacrificio` (ex.: `prateleira purga`). É assim que o
   script o reconhece.
3. **Afaste o objeto das peças**: pelo menos **73 mm** de distância entre eles. Se o objeto for mais de 25 mm mais
   alto que as peças, ele precisa ficar a pelo menos **76,5 mm** das peças na direção Y (frente/trás), por causa da
   barra do eixo X.
4. **Cor do objeto**: qualquer uma. Ele é impresso com a cor que estiver carregada em cada momento e sai listrado.
5. **Deixe desligada** a purga em enchimento, em suporte e em objeto, nas configurações de cada objeto do Studio
   ("Flush into objects' infill", "Flush into objects' support" e "Flush into this object"). O script precisa que a
   purga siga a tabela de purga do projeto.
6. **Fatie.** Abre uma janela preta: ela mostra o andamento, o que foi conferido e o antes e depois de purga, trocas e
   tempo. Leia e aperte qualquer tecla para fechar. O Studio continua sozinho.
7. **Envie pelo próprio Studio** (botão **Imprimir placa**, pela nuvem). Só assim o app Bambu Handy oferece **Pular
   objeto**. Pelo cartão SD ou pela rede local (LAN) a impressão funciona, mas sem pular objeto.

A impressora mostra o tempo novo. O Studio continua mostrando o tempo antigo, porque ele calcula antes do script.

## Segurança: o que acontece se algo der errado

Antes de gravar, o script confere o resultado: as outras peças recebem os mesmos filamentos e movimentos, a purga de
cada troca bate com a tabela do projeto, o objeto sai inteiro e o bico nunca passa baixo sobre as peças.

- **Projeto sem objeto de sacrifício:** o G-code fica como o Studio fez (impressão comum).
- **Projeto com objeto de sacrifício e qualquer falha:** o script **bloqueia**. O G-code vira um arquivo vazio, que
  não imprime nada, e o Studio mostra erro. Ele nunca imprime com a purga normal um projeto feito para zerar.
- O motivo fica em `scripts\pos_studio.log`, dentro da pasta do zera-purga.

Bloqueios comuns e o que fazer:

| Mensagem | O que fazer |
|---|---|
| Distância do objeto às peças menor que o mínimo | Afaste o objeto das peças (item 3 de "Como usar") e fatie de novo. |
| A purga das trocas não segue a matriz de purga | Desligue a purga em enchimento e em suporte (item 5) e fatie de novo. |
| Filamentos com tipo ou temperatura diferentes | O script só tira trocas entre filamentos do mesmo tipo e temperatura. Use filamentos iguais nas cores envolvidas. |
| Falta o numpy | Rode `python -m pip install numpy` e fatie de novo. |
| Não achei os objetos de sacrifício | Salve o projeto (Ctrl+S) e fatie de novo. |

O Studio só gera a lista de objetos com **2 a 64 objetos** na placa. Fora disso, o script não mexe em nada.

## Uso sem o Studio (opcional)

Para um arquivo já fatiado e exportado (**Arquivo > Exportar > Exportar arquivo fatiado da placa**):

```
python C:\zera-purga\scripts\zera_purga.py "C:\caminho\da\placa.gcode.3mf"
```

Ele grava `placa_zerado.gcode.3mf` ao lado, só se a conferência aprovar. Esse arquivo, enviado por cartão ou rede,
imprime sem a opção de pular objeto.

Se o objeto de sacrifício estiver perto demais das peças, este comando move ou reduz o objeto no projeto `.3mf`:

```
python C:\zera-purga\scripts\posiciona_sacrificio.py "C:\caminho\do\projeto.3mf" --escala auto
```

## Arquivos

| Arquivo | Função |
|---|---|
| `zera_purga_studio.bat` | Atalho que o Studio chama. Usa o PyPy se houver; senão, o Python. |
| `scripts/pos_studio.py` | Pós-processamento: acha o objeto de sacrifício, reescreve o G-code, confere e mostra a janela. |
| `scripts/purga3.py` | Separa o objeto, tira as trocas que eram só dele e encaixa a impressão dele nas trocas. |
| `scripts/reescreve.py` | Funções de leitura e escrita do G-code. |
| `scripts/confere3.py` | Conferência do resultado. |
| `scripts/tempo.py` | Recalcula o tempo que a impressora mostra. |
| `scripts/paralelo.py` | Roda as conferências em paralelo. |
| `scripts/zera_purga.py` | Uso sem o Studio, em arquivo exportado. |
| `scripts/posiciona_sacrificio.py`, `scripts/le3mf.py` | Leitura do projeto `.3mf` e reposicionamento do objeto. |
