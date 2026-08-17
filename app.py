"""
Bloque de Evaluación y Calidad — TFG Íñigo Daza
Evaluación de la salida del módulo de auditoría de pedidos (Juan Salas · GraphyCems).

Flujo:
  1. Se suben los documentos del pedido.
  2. Se introduce lo que reportó el módulo de Juan.
  3. El evaluador calcula por su cuenta las discrepancias reales, contrasta la
     salida del módulo contra ese cálculo y emite el EvaluationResult.

El evaluador nunca toma como referencia lo que dice el módulo evaluado.
"""

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import evaluador as E

st.set_page_config(page_title="Evaluación y Calidad — TFG", layout="wide")

COLOR = {"pasa": "#1a7f37", "no_pasa": "#c0392b", "pendiente": "#7d7d7d"}
TEXTO = {"pasa": "Pasa", "no_pasa": "No pasa", "pendiente": "Pendiente"}

CAMPO_POR_ETIQUETA = {v: k for k, v in E.ETIQUETAS.items()}
OPCIONES_CAMPO = list(E.ETIQUETAS.values())
OPCIONES_CORREGIR = ["Orden de fabricación", "Pedido de cliente", "No lo declara"]
MAPA_CORREGIR = {"Orden de fabricación": "orden", "Pedido de cliente": "cliente",
                 "No lo declara": None}

st.title("Bloque de Evaluación y Calidad")
st.caption("Evaluación de la salida del módulo de auditoría de pedidos · "
           "Juan Salas · GraphyCems")

if not E.hay_pdftotext():
    st.error("No se encuentra `pdftotext`. En Streamlit Cloud, añade un fichero "
             "`packages.txt` con la línea `poppler-utils` y vuelve a desplegar.")
    st.stop()

# ===========================================================================
# Paso 1 — Documentos del pedido
# ===========================================================================
st.header("1 · Documentos del pedido")
st.write("Sube la orden de fabricación y la documentación de cliente. "
         "Cada documento se clasifica por su contenido, no por el nombre del fichero.")

subidos = st.file_uploader("Documentos en PDF", type="pdf",
                           accept_multiple_files=True)

if not subidos:
    st.info("Esperando documentos.")
    st.stop()

docs = []
with tempfile.TemporaryDirectory() as tmp:
    for f in subidos:
        ruta = Path(tmp) / f.name
        ruta.write_bytes(f.getbuffer())
        capa = E.tiene_capa_texto(ruta)
        texto = E.texto_pdf(ruta) if capa else ""
        docs.append({"nombre": f.name, "tipo": E.clasificar(texto) if capa else "sin_texto",
                     "texto": texto, "capa": capa})

TIPOS = {"orden": "Orden de fabricación", "pedido_cliente": "Pedido de cliente",
         "presupuesto": "Presupuesto", "desconocido": "No identificado",
         "sin_texto": "Sin capa de texto"}

st.dataframe(pd.DataFrame([{"Fichero": d["nombre"], "Identificado como": TIPOS[d["tipo"]]}
                           for d in docs]),
             use_container_width=True, hide_index=True)

sin_capa = [d["nombre"] for d in docs if not d["capa"]]
if sin_capa:
    st.warning(f"Sin capa de texto extraíble: {', '.join(sin_capa)}. "
               "Un documento escaneado no puede auditarse ni evaluarse.")

orden_doc = next((d for d in docs if d["tipo"] == "orden"), None)
cliente_docs = [d for d in docs if d["tipo"] in ("pedido_cliente", "presupuesto")]

if not orden_doc:
    st.error("Falta la orden de fabricación: es el documento que se audita.")
    st.stop()
if not cliente_docs:
    st.error("Falta documentación de cliente: sin ella no hay contra qué contrastar.")
    st.stop()

campos_orden = E.campos_orden(orden_doc["texto"])
campos_cliente = {}
for d in cliente_docs:                      # los datos pueden venir repartidos
    for k, v in E.campos_cliente(d["texto"]).items():
        campos_cliente.setdefault(k, v)

with st.expander("Campos extraídos de los documentos", expanded=False):
    filas = []
    for k, etiqueta in E.ETIQUETAS.items():
        filas.append({"Campo": etiqueta,
                      "Documentación de cliente": campos_cliente.get(k, "—"),
                      "Orden de fabricación": campos_orden.get(k, "—")})
    st.table(pd.DataFrame(filas))
    respaldos = {k: v for k, v in campos_orden.items()
                 if k in ("cantidad_logistica", "cantidad_impresion")}
    if respaldos:
        st.caption("Valores de respaldo hallados dentro de la propia orden de fabricación: "
                   + " · ".join(f"{k.replace('cantidad_', '')}: {v}" for k, v in respaldos.items()))

comparables = E.campos_comparables(campos_orden, campos_cliente)
if not comparables:
    st.error("No hay ningún campo presente en ambos documentos. No se puede evaluar.")
    st.stop()

# ===========================================================================
# Paso 2 — Salida del módulo
# ===========================================================================
st.header("2 · Respuesta del módulo de Juan")
st.write("Pega la respuesta tal como aparece en la interfaz de GraphyCems, con sus "
         "encabezados de severidad. El evaluador la interpreta y te muestra qué ha "
         "entendido antes de puntuar.")

EJEMPLO_42805 = """INCONGRUENCIA (1)
El pedido del cliente (Beliefs in Our World 2nd Edition Skills Book 9780717195473.pdf) indica 3.000 unidades, pero la orden de fabricación (of42805.pdf) indica 30.000.
of42805.pdf   Beliefs in Our World 2nd Edition Skills Book 9780717195473.pdf

A REVISAR (1)
El gramaje de cubierta en el pedido del cliente (Beliefs in Our World 2nd Edition Skills Book 9780717195473.pdf) es 240g, pero la orden de fabricación (of42805.pdf) indica 250g. A revisar: podría ser el redondeo estándar de GraphyCems, no necesariamente un error.
of42805.pdf   Beliefs in Our World 2nd Edition Skills Book 9780717195473.pdf"""

if "respuesta" not in st.session_state:
    st.session_state.respuesta = ""

b1, b2 = st.columns([1, 3])
if b1.button("Pegar la respuesta del 42805"):
    st.session_state.respuesta = EJEMPLO_42805
b2.caption("Atajo para la demostración: carga la respuesta que el módulo emitió "
           "sobre el pedido 42805.")

respuesta = st.text_area(
    "Respuesta del módulo",
    key="respuesta", height=220,
    placeholder="INCONGRUENCIA (1)\nEl pedido del cliente (...) indica 3.000 unidades, "
                "pero la orden de fabricación (...) indica 30.000.\n\nA REVISAR (1)\n...",
    help="Si el módulo no reportó nada, escribe su mensaje de ausencia de incidencias "
         "o deja el campo vacío: el evaluador comprobará si esa ausencia era correcta.",
)

incidencias, avisos_lectura = E.interpretar(respuesta)

for a in avisos_lectura:
    st.warning(a)

st.markdown("**Interpretación de la respuesta**")
if incidencias:
    st.caption("Revisa que coincide con lo que dice el módulo. Puedes corregir cualquier "
               "celda antes de evaluar.")
    base = pd.DataFrame([{
        "Campo": E.ETIQUETAS.get(i["campo"], i["campo"]),
        "Valor según cliente": i["valor_cliente"] or "",
        "Valor según orden": i["valor_orden"] or "",
        "Severidad": "Alta" if i["severidad"] == "alta" else "Menor",
        "Cita ambos documentos": i["cita_documentos"],
        "Documento a corregir": {"orden": "Orden de fabricación",
                                 "cliente": "Pedido de cliente"}.get(i["corregir"], "No lo declara"),
        "Señala incoherencia interna": i["interna"],
    } for i in incidencias])

    editadas = st.data_editor(
        base, num_rows="dynamic", use_container_width=True, hide_index=True,
        key="revision",
        column_config={
            "Campo": st.column_config.SelectboxColumn(options=OPCIONES_CAMPO, required=True),
            "Severidad": st.column_config.SelectboxColumn(options=["Alta", "Menor"], required=True),
            "Cita ambos documentos": st.column_config.CheckboxColumn(
                help="¿La incidencia referencia el documento de origen y el de destino?"),
            "Documento a corregir": st.column_config.SelectboxColumn(options=OPCIONES_CORREGIR),
            "Señala incoherencia interna": st.column_config.CheckboxColumn(
                help="¿Advierte de que la orden se contradice a sí misma?"),
        },
    )

    incidencias = []
    for _, f in editadas.iterrows():
        campo = CAMPO_POR_ETIQUETA.get(str(f["Campo"]))
        if not campo:
            continue
        incidencias.append({
            "campo": campo,
            "valor_cliente": (str(f["Valor según cliente"]).strip() or None),
            "valor_orden": (str(f["Valor según orden"]).strip() or None),
            "severidad": "alta" if str(f["Severidad"]) == "Alta" else "menor",
            "cita_documentos": bool(f["Cita ambos documentos"]),
            "corregir": MAPA_CORREGIR.get(str(f["Documento a corregir"])),
            "interna": bool(f["Señala incoherencia interna"]),
        })
elif respuesta.strip():
    st.info("No se ha reconocido ninguna incidencia en la respuesta. Si el módulo "
            "efectivamente no reportó nada, continúa: el evaluador comprobará si esa "
            "ausencia era correcta.")
else:
    st.info("Sin respuesta introducida. Si continúas, se evaluará como ausencia de incidencias.")

with st.expander("Cómo se interpreta la respuesta"):
    st.markdown(
        "La lectura es determinista: reconoce los encabezados de severidad "
        "(`INCONGRUENCIA`, `A REVISAR`), identifica el campo por las expresiones que "
        "emplea el módulo, separa los dos valores en conflicto por la conjunción "
        "adversativa y atribuye cada uno a su documento según cuál se mencione en cada "
        "lado.\n\n"
        "Cubre la forma en que el módulo redacta hoy. Para formulaciones arbitrarias, "
        "aquí es donde encajaría una llamada a un modelo de lenguaje, sustituyendo el "
        "intérprete sin alterar el resto del evaluador: lo que se juzga son las "
        "incidencias ya interpretadas, no el texto."
    )

# ===========================================================================
# Paso 3 — Evaluación
# ===========================================================================
st.header("3 · Evaluación")

if not st.button("Evaluar la salida del módulo", type="primary"):
    st.stop()

pedido = Path(orden_doc["nombre"]).stem
ev = E.evaluar(campos_orden, campos_cliente, incidencias)
er = E.evaluation_result(ev, pedido)
c = ev["contraste"]

# --- Contraste independiente ------------------------------------------------
st.subheader("Contraste independiente")
st.caption("El evaluador calcula las discrepancias leyendo los documentos. "
           "Las cifras siguientes no proceden del módulo evaluado.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Exhaustividad", f"{c['exhaustividad']}%" if c["exhaustividad"] is not None else "—",
          help="De las discrepancias que existen en los documentos, cuántas encontró el módulo.")
m2.metric("Precisión", f"{c['precision']}%" if c["precision"] is not None else "—",
          help="De las incidencias que emitió el módulo, cuántas se sostienen documentalmente.")
m3.metric("Discrepancias reales", len(ev["discrepancias_reales"]))
m4.metric("Incidencias del módulo", len(incidencias))

if ev["discrepancias_reales"]:
    st.markdown("**Discrepancias calculadas por el evaluador**")
    st.table(pd.DataFrame([{
        "Campo": d["etiqueta"], "Cliente": d["valor_cliente"],
        "Orden de fabricación": d["valor_orden"],
        "Severidad esperada": d["severidad_esperada"].capitalize(),
        "Detectada por el módulo": "Sí" if d in c["detectadas"] else "No",
    } for d in ev["discrepancias_reales"]]))
else:
    st.success("Los documentos concuerdan en todos los campos comparables: "
               "no había ninguna discrepancia que detectar.")

if c["omitidas"]:
    st.error("Discrepancias existentes que el módulo no reportó: "
             + ", ".join(d["etiqueta"] for d in c["omitidas"]))
if c["falsas"]:
    st.error("Incidencias emitidas que no se sostienen en los documentos: "
             + ", ".join(E.ETIQUETAS.get(i["campo"], i["campo"]) for i in c["falsas"]))
if ev["mal_citados"]:
    st.warning("Incidencias cuyos valores citados no coinciden con los documentos: "
               + "; ".join(f"{v['etiqueta']} cita {v['citado']} y los documentos dicen {v['real']}"
                           for v in ev["mal_citados"]))

if ev["incoherencias_internas"]:
    st.markdown("**Incoherencias internas de la orden de fabricación**")
    st.table(pd.DataFrame([{
        "Campo": i["etiqueta"], "Valor en cabecera": i["valor_cabecera"],
        "Valor de respaldo": i["valor_respaldo"], "Procedencia del respaldo": i["donde"],
    } for i in ev["incoherencias_internas"]]))
    st.caption("El cálculo productivo del propio documento contradice su cabecera: "
               "la orden contenía la prueba de cuál era el valor correcto.")

# --- Resultado caso a caso --------------------------------------------------
st.subheader("Resultado caso a caso")
r = er["resumen"]
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Casos", r["total"])
k2.metric("Con evidencia", r["con_evidencia"])
k3.metric("Superados", r["pasa"])
k4.metric("Fallidos", r["no_pasa"])
k5.metric("Pendientes", r["pendiente"])

df = pd.DataFrame([{
    "#": n, "Caso": E.CASOS[n], "Resultado": TEXTO[caso["resultado"]],
    "Observación": caso["observacion"],
} for n, caso in sorted(ev["casos"].items())])

INVERSO = {v: k for k, v in TEXTO.items()}
st.dataframe(
    df.style.map(lambda v: f"color: {COLOR[INVERSO[v]]}; font-weight: 600"
                 if v in INVERSO else "", subset=["Resultado"]),
    use_container_width=True, hide_index=True,
)
if r["tasa"] is not None:
    st.caption(f"Tasa de acierto sobre casos verificados: **{r['tasa']}%**. "
               "Los casos pendientes quedan excluidos del cálculo: contarlos como "
               "superados inflaría el resultado.")

# --- EvaluationResult -------------------------------------------------------
st.subheader("EvaluationResult")
st.write(er["valoracion"])

if er["aspectos"]:
    st.markdown("**Aspectos a mejorar**")
    for i, a in enumerate(er["aspectos"], 1):
        etq = E.ETIQUETA_CASO[a["estado"]]
        with st.expander(f"{i}. {a['titulo']}  ·  caso {a['caso']} ({etq})"):
            st.write(a["detalle"])
            st.markdown(f"**Corrección propuesta:** {a['correccion']}")
else:
    st.success("No se han emitido aspectos a mejorar.")

# --- Exportación ------------------------------------------------------------
st.subheader("Exportar")
e1, e2 = st.columns(2)
e1.download_button("EvaluationResult (Markdown)",
                   E.a_markdown(er, ev).encode("utf-8"),
                   file_name=f"evaluationresult_{pedido}.md", mime="text/markdown")
e2.download_button("Resultado caso a caso (CSV)",
                   df.to_csv(index=False).encode("utf-8-sig"),
                   file_name=f"casos_{pedido}.csv", mime="text/csv")sos_{pedido}.csv", mime="text/csv")
