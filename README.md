# Giro Reverso

**Logística reversa de estoque parado numa rede de autopeças: o que deve voltar da loja para o CD, e onde está cada pedido.**

App em Streamlit para uma rede com várias lojas e um centro de distribuição. Uma regra estatística de estoque decide **o que** volta
de cada loja, um ciclo diário distribui o trabalho entre os compradores e um quadro por etapa acompanha cada pedido de devolução até
o estoque ser reintegrado no CD.

> **Dados sintéticos.** A rede (5 lojas e 1 CD), os produtos, fabricantes, vendas e pedidos são fictícios, gerados por um ERP
> sintético reprodutível (`data/erp.py`).

## O problema

Peça parada na loja é capital imobilizado. Devolvê-la ao CD libera esse capital para o resto da rede, mas:

- devolver demais gera frete de ida e volta: se o que fica cai abaixo do necessário até a próxima reposição, o CD manda a peça de volta;
- o processo cruza Compras, Logística da loja, Logística do CD e Qualidade, e sem um lugar único ninguém sabe onde cada pedido trava.

## A regra: estoque-alvo por revisão periódica

Para cada item de cada loja (`domain/regra.py`):

| Passo | Cálculo |
|---|---|
| Previsão | μ = suavização exponencial simples das vendas dos últimos 12 meses (α = 0,3) |
| Incerteza | σ = desvio-padrão dos erros de previsão um passo à frente, com piso de Poisson √μ |
| Alvo | S = d·(C + L) + z·σ·√((C + L)/30), com d = μ/30, C a cobertura-alvo, L o prazo de reposição e z = Φ⁻¹(nível de serviço) |
| Ajustes | S sobe para o mínimo de exposição e para o próximo múltiplo de transferência |
| Retirar | estoque − S, arredondado para baixo no múltiplo (não se abre embalagem) |
| Exclusões | nada a retirar → lançamento (< 90 dias de loja) → sazonal → em promoção → abaixo do valor mínimo |

O motivo classifica o que volta: **sem venda em 6 meses**, **sem venda em 90 dias** ou **excesso sobre o alvo**. A Gestão ajusta nível de
serviço, cobertura e valor mínimo na Visão de gestão. A tela **Sensibilidade da regra** mostra o efeito de cada combinação: mais nível
de serviço ou mais cobertura seguram mais estoque na loja e devolvem menos capital.

## O fluxo

| Tela | O que faz |
|---|---|
| **Identificação por loja** | O resultado da regra por loja, com filtros e a ficha de cada item: a conta do alvo, as vendas mensais, o estoque na rede. |
| **Lista diária** | O que volta hoje, por motivo e por origem do estoque. |
| **Ciclo diário** | A Gestão abre o ciclo (pessoas × pedidos × SKUs por dia); a cada dia o app **revalida** os candidatos com o estoque e a venda de agora, monta o lote e distribui entre os compradores. |
| **Vínculo** | O comprador emite o pedido no ERP e informa o número; o app confere (existe, não está cancelado, sem produto fora do lote nem quantidade acima do "Retirar"). Na demonstração, o botão **Emitir no ERP simulado e vincular** faz o papel do ERP. |
| **Acompanhamento** | Kanban em 6 etapas: Emitido → Separação → Saída da loja → Chegada no CD → Conferência → Reintegração. Pedido parado há mais de 48 h fica em atenção. |
| **Visão de gestão** | Parâmetros da regra, tempo médio por etapa (o gargalo), ciclo médio contra a meta e vínculos com problema. |
| **Perfil e histórico da loja** | Cobertura do estoque, CMV mensal, concentração por categoria e fabricante, histórico de devoluções. |

Cada perfil (Compras, Logística da loja, Logística do CD, Qualidade, Gestão) só registra a etapa que é sua; valores em R$ só aparecem
para Compras e Gestão.

## Dados sintéticos

`data/erp.py` gera, com semente fixa, ~1.200 itens em 14 categorias e ~3.300 combinações loja × item. Cada combinação tem um perfil de
demanda (ativo com sazonalidade leve, em queda, parado há alguns meses ou sem venda no ano); a venda de cada mês é Poisson da demanda
daquele mês. O estoque segue a demanda, com uma parte em excesso e o residual dos itens parados. Também há prazo de reposição por loja,
mínimo de exposição, múltiplo de transferência, tempo de loja, sazonalidade, promoção e origem do estoque.

## Como rodar

```bash
pip install -r requirements.txt          # ou: uv sync
streamlit run streamlit_app.py
python -m pytest                         # 220+ testes, incluindo todas as telas rodando com AppTest
ruff check .
```

Entre como **Gestão** para ver tudo. Em *Visão de gestão*, "Recriar pedidos de exemplo" devolve o quadro ao estado inicial.

## Demonstração estática (GitHub Pages)

Para ver o app sem servidor, há uma versão estática no portfólio: a mesma regra reescrita em JavaScript, rodando no navegador sobre a
base sintética, com a Visão de gestão (parâmetros ajustáveis), a Lista diária com a ficha de cada item, a Sensibilidade, o Perfil da
loja e o Acompanhamento (pedidos de exemplo). Os dados saem daqui:

```bash
python ferramentas/exportar_demo_estatica.py PASTA   # grava PASTA/dados.js e PASTA/referencia.json
```

`referencia.json` é o resultado da regra em Python com os parâmetros padrão; a versão em JavaScript foi conferida contra ele item a
item (mesmo alvo, mesma quantidade a retirar, mesma decisão e mesmo motivo nos 3.259 itens).

## Estrutura

```
app.py, streamlit_app.py   login, navegação por papel e ponto de entrada
telas/                     uma tela por arquivo
domain/                    regras em funções puras (pandas/NumPy/Polars), testadas
  regra.py                 previsão, estoque de segurança, alvo, exclusões
  sensibilidade.py         grade nível de serviço × cobertura
  ciclo.py                 fila, revalidação, lote e distribuição
  acompanhamento.py        etapa atual, tempo parado, KPIs
data/
  erp.py                   ERP sintético (catálogo, vendas, estoque, pedidos de devolução)
  estado.py                banco de acompanhamento (SQLAlchemy: SQLite local ou Postgres)
ui/                        tema, números em português, quadro kanban (componente HTML), ficha do item, cache
tests/                     pytest
```

Detalhes de engenharia: a base do ERP fica em `st.cache_resource` e a regra roda numa segunda camada de cache, por parâmetros (trocar
um parâmetro não relê o ERP); gravações concorrentes protegidas (duas sessões no "Gerar Dia" ou no mesmo vínculo não se atropelam);
migração aditiva de schema na subida.

## Stack

Python 3.12+ · Streamlit · pandas · NumPy · Polars · SQLAlchemy · pytest · Ruff
