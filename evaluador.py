"""
Evaluador del módulo de auditoría de pedidos (Juan Salas · GraphyCems).

Principio de funcionamiento: el evaluador no se fía de lo que reporta el módulo.
Lee los documentos de origen, calcula por su cuenta qué discrepancias existen
realmente, y sólo entonces contrasta la salida del módulo contra ese cálculo.

De ahí salen las dos magnitudes del veredicto:
  exhaustividad : de las discrepancias que existían, cuántas encontró el módulo
  precisión     : de las incidencias que emitió, cuántas se sostienen documentalmente
"""

import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

# ===========================================================================
# 1. Lectura de PDF
# ===========================================================================

def hay_pdftotext():
    return shutil.which("pdftotext") is not None


def texto_pdf(ruta):
    """Texto del PDF preservando la disposición en columnas."""
    r = subprocess.run(["pdftotext", "-layout", str(ruta), "-"],
                       capture_output=True, text=True)
    return r.stdout


def tiene_capa_texto(ruta):
    """False si el PDF es un escaneo sin texto extraíble."""
    r = subprocess.run(["pdffonts", str(ruta)], capture_output=True, text=True)
    return len(r.stdout.strip().splitlines()) > 2


def clasificar(texto):
    """Identifica de qué tipo es un documento por su contenido, no por su nombre."""
    t = texto.lower()
    if "orden de fabricacion" in t or "orden de fabricación" in t:
        return "orden"
    if "quantity:" in t or "cover material:" in t:
        return "pedido_cliente"
    if "please find herewith our prices" in t or re.search(r"\bcps\.\s*=", t):
        return "presupuesto"
    return "desconocido"


# ===========================================================================
# 2. Normalización
# ===========================================================================

def numero(s):
    """'30.000' -> 30000 · '3,000' -> 3000 · '240gsm' -> 240"""
    if s is None:
        return None
    s = re.sub(r"[^\d.,]", "", str(s)).replace(".", "").replace(",", "")
    return int(s) if s.isdigit() else None


def formato_normal(v):
    """'297 x 210' y '210x297' son el mismo formato: se ordenan para comparar."""
    n = re.findall(r"\d+", str(v or ""))
    return "x".join(sorted(n, key=int)) if n else None


# ===========================================================================
# 3. Extracción de campos
# ===========================================================================

def campos_orden(texto):
    """
    Campos de la orden de fabricación.

    Además del valor de cabecera se recogen los valores de respaldo que aparecen
    en otras secciones del mismo documento (logística, tabla de impresión). Son
    los que permiten detectar que el documento se contradice a sí mismo.
    """
    c = {}

    m = re.search(r"Cantidad:\s*\n?.*?\n\s*\S.*?\s{2,}([\d.,]+)\s+(\d+)\s*$",
                  texto, re.MULTILINE)
    if m:
        c["cantidad"] = numero(m.group(1))
        c["paginas"] = numero(m.group(2))

    pos = texto.find("LOG")
    if pos != -1:
        m = re.search(r"Cantidad:\s*([\d.,]+)", texto[pos:])
        if m:
            c["cantidad_logistica"] = numero(m.group(1))

    m = re.search(r"Interiores\s+\d+\s+\d+\s+\S+\s+\S+\s+\d+\s+([\d.,]+)\s+([\d.,]+)", texto)
    if m:
        c["cantidad_impresion"] = numero(m.group(1))

    for destino, clave in (("Interiores", "gramaje_interior"),
                           ("Cubiertas", "gramaje_cubierta")):
        m = re.search(rf"^\s*\S.*?\s{{2,}}[\d ]+x[\d ]+\s+([\d.,]+)\s+{destino}\s",
                      texto, re.MULTILINE)
        if m:
            c[clave] = numero(m.group(1))

    m = re.search(r"^(\d{13})\s+(\d+\s*x\s*\d+)", texto, re.MULTILINE)
    if m:
        c["isbn"] = m.group(1)
        c["formato"] = formato_normal(m.group(2))

    return c


def campos_cliente(texto):
    """
    Campos de la documentación de cliente. Cubre los dos formatos observados:
    la carta de pedido y el presupuesto, que expresan los mismos datos
    de forma distinta.
    """
    c = {}

    # --- Carta de pedido
    directos = {
        "cantidad":         r"Quantity:\s*([\d.,]+)\s*copies",
        "paginas":          r"Extent:\s*([\d.,]+)\s*pp",
        "gramaje_cubierta": r"Cover Material:\s*([\d.,]+)\s*gsm",
        "gramaje_interior": r"Text Paper:\s*([\d.,]+)\s*gsm",
    }
    for clave, patron in directos.items():
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            c[clave] = numero(m.group(1))

    m = re.search(r"RE:\s*(\d{13})", texto)
    if m:
        c["isbn"] = m.group(1)
    m = re.search(r"Trimmed Size:\s*([\d\sx]+)mm", texto, re.IGNORECASE)
    if m:
        c["formato"] = formato_normal(m.group(1))

    # --- Presupuesto
    if "cantidad" not in c:
        m = re.search(r"([\d.,]+)\s*cps\.\s*=", texto)
        if m:
            c["cantidad"] = numero(m.group(1))
    if "paginas" not in c:
        m = re.search(r"Extent\s+([\d.,]+)\s*pp", texto, re.IGNORECASE)
        if m:
            c["paginas"] = numero(m.group(1))
    if "gramaje_cubierta" not in c:
        m = re.search(r"Cover:.*?([\d.,]+)\s*gsm", texto, re.IGNORECASE)
        if m:
            c["gramaje_cubierta"] = numero(m.group(1))
    if "gramaje_interior" not in c:
        m = re.search(r"Inside:.*?([\d.,]+)\s*gsm", texto, re.IGNORECASE)
        if m:
            c["gramaje_interior"] = numero(m.group(1))
    if "isbn" not in c:
        m = re.search(r"Ref:\s*(\d{13})", texto)
        if m:
            c["isbn"] = m.group(1)
    if "formato" not in c:
        m = re.search(r"TPS\s+([\d\sx]+)mm", texto, re.IGNORECASE)
        if m:
            c["formato"] = formato_normal(m.group(1))

    return c


# ===========================================================================
# 4. Cálculo independiente de la verdad de campo
# ===========================================================================

# Severidad esperada cuando el campo difiere. "menor" recoge las diferencias con
# explicación industrial plausible: el gramaje de cartulina se redondea al
# formato comercial disponible, luego 240 frente a 250 no es necesariamente un error.
CAMPOS = {
    "cantidad":         ("Cantidad", "alta"),
    "paginas":          ("Páginas", "alta"),
    "isbn":             ("ISBN", "alta"),
    "formato":          ("Formato", "alta"),
    "gramaje_interior": ("Gramaje de interior", "menor"),
    "gramaje_cubierta": ("Gramaje de cubierta", "menor"),
}

ETIQUETAS = {k: v[0] for k, v in CAMPOS.items()}


def discrepancias_reales(orden, cliente):
    """Discrepancias entre orden y cliente, calculadas por el evaluador."""
    out = []
    for clave, (etiqueta, severidad) in CAMPOS.items():
        a, b = cliente.get(clave), orden.get(clave)
        if a is None or b is None:
            continue
        if str(a) != str(b):
            out.append({"campo": clave, "etiqueta": etiqueta,
                        "valor_cliente": a, "valor_orden": b,
                        "severidad_esperada": severidad})
    return out


def incoherencias_internas(orden):
    """
    Contradicciones de la orden consigo misma. Un valor de cabecera que no
    concuerda con el cálculo productivo del propio documento delata un error
    de transcripción sin necesidad de consultar ninguna otra fuente.
    """
    out = []
    cab = orden.get("cantidad")
    for clave, donde in (("cantidad_logistica", "bloque de logística"),
                         ("cantidad_impresion", "tabla de impresión")):
        v = orden.get(clave)
        if cab is not None and v is not None and cab != v:
            out.append({"campo": "cantidad", "etiqueta": "Cantidad",
                        "valor_cabecera": cab, "valor_respaldo": v, "donde": donde})
    return out


def campos_comparables(orden, cliente):
    return [k for k in CAMPOS if orden.get(k) is not None and cliente.get(k) is not None]


# ===========================================================================
# 5. Contraste con la salida del módulo
# ===========================================================================

def contrastar(incidencias, reales):
    reportados = {i.get("campo") for i in incidencias}
    esperados = {d["campo"] for d in reales}

    detectadas = [d for d in reales if d["campo"] in reportados]
    omitidas = [d for d in reales if d["campo"] not in reportados]
    falsas = [i for i in incidencias if i.get("campo") not in esperados]
    n = len(incidencias)

    return {
        "detectadas": detectadas, "omitidas": omitidas, "falsas": falsas,
        "exhaustividad": round(100 * len(detectadas) / len(reales), 1) if reales else None,
        "precision": round(100 * (n - len(falsas)) / n, 1) if n else None,
    }


def severidades(incidencias, reales):
    esperada = {d["campo"]: d["severidad_esperada"] for d in reales}
    return [{"campo": i["campo"], "etiqueta": ETIQUETAS.get(i["campo"], i["campo"]),
             "asignada": i.get("severidad"), "esperada": esperada[i["campo"]],
             "correcta": i.get("severidad") == esperada[i["campo"]]}
            for i in incidencias if i.get("campo") in esperada]


def valores_correctos(incidencias, reales):
    """Comprueba si los valores que cita el módulo coinciden con los documentos."""
    ref = {d["campo"]: d for d in reales}
    out = []
    for i in incidencias:
        d = ref.get(i.get("campo"))
        if not d:
            continue
        vc, vo = i.get("valor_cliente"), i.get("valor_orden")
        ok = ((vc is None or str(numero(vc) or vc) == str(d["valor_cliente"])) and
              (vo is None or str(numero(vo) or vo) == str(d["valor_orden"])))
        out.append({"campo": i["campo"], "etiqueta": d["etiqueta"], "correcto": ok,
                    "citado": f"{vc} / {vo}",
                    "real": f"{d['valor_cliente']} / {d['valor_orden']}"})
    return out


# ===========================================================================
# 5. Interpretación de la respuesta del módulo
# ===========================================================================

# Reglas de lectura de la respuesta en lenguaje natural. Es un intérprete
# determinista: reconoce la forma en que el módulo redacta hoy sus incidencias.
# Cuando haya que cubrir formulaciones arbitrarias, este es el punto donde
# encaja una llamada a un modelo de lenguaje, sustituyendo interpretar() sin
# tocar nada más del evaluador.

PISTAS_CAMPO = [
    ("gramaje_cubierta", r"gramaje\s+de\s+(la\s+)?cubierta|cubierta.{0,30}gramaje|gramaje.{0,20}cubierta"),
    ("gramaje_interior", r"gramaje\s+de\s+(l\s*)?interior|papel\s+de\s+interior|interior.{0,20}gramaje"),
    ("cantidad",         r"\bunidades\b|\bcantidad\b|\btirada\b|\bejemplares\b|\bcopias\b"),
    ("paginas",          r"\bp[áa]ginas\b|\bpp\b|\bextent\b"),
    ("isbn",             r"\bisbn\b"),
    ("formato",          r"\bformato\b|\btama[ñn]o\b|\bmedidas\b"),
]

SIN_INCIDENCIAS = r"no\s+se\s+han?\s+(encontrado|detectado)|sin\s+incongruencias|ninguna\s+incidencia|todo\s+(es\s+)?correcto|no\s+hay\s+(incongruencias|discrepancias)"


def _campo_de(texto):
    t = texto.lower()
    for campo, patron in PISTAS_CAMPO:      # el orden importa: lo específico primero
        if re.search(patron, t):
            return campo
    return None


def _severidad_de(cabecera, cuerpo):
    t = (cabecera + " " + cuerpo).lower()
    if re.search(r"a\s+revisar|revisar:|posible|podr[íi]a\s+ser|no\s+necesariamente", t):
        return "menor"
    if re.search(r"incongruencia|discrepancia|error|incoherencia", t):
        return "alta"
    return None


def _bloques(texto):
    """
    Parte la respuesta en incidencias. Reconoce dos formas: encabezados de
    severidad seguidos de párrafos, y listas de párrafos sin encabezado.
    """
    lineas = [l.rstrip() for l in texto.splitlines()]
    bloques, cabecera, actual = [], "", []

    def cerrar():
        cuerpo = " ".join(x.strip() for x in actual if x.strip())
        if len(cuerpo) > 25:                       # descarta restos de interfaz
            bloques.append((cabecera, cuerpo))

    for linea in lineas:
        desnuda = linea.strip()
        if re.match(r"^\s*(INCONGRUENCIA|A\s+REVISAR|INCOHERENCIA|AVISO|ERROR)S?\b",
                    desnuda, re.IGNORECASE):
            cerrar()
            cabecera, actual = desnuda, []
            continue
        if not desnuda:                            # línea en blanco separa incidencias
            if actual:
                cerrar()
                actual = []
            continue
        actual.append(desnuda)
    cerrar()
    return bloques


def interpretar(texto):
    """
    Convierte la respuesta del módulo, tal como aparece en su interfaz, en la
    lista de incidencias que consume el evaluador.

    Devuelve (incidencias, avisos). Los avisos recogen lo que no ha podido
    interpretarse, para que quede a la vista y pueda corregirse a mano.
    """
    texto = (texto or "").strip()
    if not texto:
        return [], []
    if re.search(SIN_INCIDENCIAS, texto, re.IGNORECASE) and len(texto) < 200:
        return [], []

    incidencias, avisos = [], []
    for cabecera, cuerpo in _bloques(texto):
        campo = _campo_de(cuerpo)
        if not campo:
            avisos.append(f"No se ha identificado el campo en: «{cuerpo[:80]}…»")
            continue

        # El módulo redacta "el cliente indica X, pero la orden indica Y".
        # La conjunción separa el valor de origen del valor discrepante.
        partes = re.split(r"\bpero\b|\bmientras que\b|\bfrente a\b", cuerpo, maxsplit=1)
        izq, der = (partes[0], partes[1]) if len(partes) == 2 else (cuerpo, "")

        def primer_numero(fragmento):
            # Se descartan los números pegados a un nombre de fichero (ISBN del título)
            limpio = re.sub(r"\S+\.pdf", " ", fragmento, flags=re.IGNORECASE)
            m = re.search(r"(?:indica|es|de|señala|pone|figura|consta)\s+([\d.,]+)", limpio)
            if not m:
                m = re.search(r"\b([\d.,]{2,})\b", limpio)
            if not m:
                return None
            return m.group(1).rstrip(".,;:")        # la puntuación de la frase no es parte del valor

        v_izq, v_der = primer_numero(izq), primer_numero(der)

        # Quién es quién: el fragmento que menciona la orden aporta el valor de la orden
        menciona_orden = lambda s: bool(re.search(r"orden\s+de\s+fabricaci[óo]n|\bof\d*\.pdf|\bOF\b", s, re.IGNORECASE))
        if menciona_orden(der) or not menciona_orden(izq):
            valor_cliente, valor_orden = v_izq, v_der
            corregir = "orden"
        else:
            valor_cliente, valor_orden = v_der, v_izq
            corregir = "cliente"

        ficheros = {f.strip().lower() for f in re.findall(r"[\w\s\-–—()]+?\.pdf", cuerpo)}
        severidad = _severidad_de(cabecera, cuerpo)
        if severidad is None:
            severidad = "alta"
            avisos.append(f"Severidad no declarada en la incidencia de "
                          f"{ETIQUETAS.get(campo, campo)}; se asume alta.")

        # Incoherencia interna: ambos valores atribuidos al mismo documento.
        # En ese caso la dirección de la corrección no aplica: no hay dos
        # documentos entre los que elegir cuál es la fuente de verdad.
        interna = bool(re.search(r"cabecera|logística|log[íi]stica|internamente|"
                                 r"el propio documento|dentro del mismo", cuerpo, re.IGNORECASE))
        if interna:
            corregir = None

        incidencias.append({
            "campo": campo, "valor_cliente": valor_cliente, "valor_orden": valor_orden,
            "severidad": severidad, "cita_documentos": len(ficheros) >= 2,
            "corregir": corregir, "interna": interna, "texto": cuerpo,
        })

    return incidencias, avisos


# ===========================================================================
# 6. Batería: resultado por caso
# ===========================================================================

CASOS = {
    1:  "Detección de discrepancias de severidad alta",
    2:  "Graduación de la severidad",
    3:  "Ausencia de incidencias sin respaldo documental",
    4:  "Repetibilidad del veredicto",
    5:  "Coherencia interna del documento auditado",
    6:  "Trazabilidad de la evidencia",
    7:  "Dirección de la corrección",
    8:  "Cobertura de campos del módulo",
    9:  "Distinción entre ausencia de incidencias e imposibilidad de comprobar",
    10: "Alcance del motor según el flujo",
}


def _r(ok, detalle, omitir=False):
    return {"resultado": "pendiente" if omitir else ("pasa" if ok else "no_pasa"),
            "observacion": detalle}


def evaluar(orden, cliente, incidencias, nombre_orden="la orden de fabricación"):
    """
    Ejecuta la batería sobre un pedido. Todos los resultados se calculan a partir
    de los documentos; ninguno se lee de un fichero de resultados previo.
    """
    reales = discrepancias_reales(orden, cliente)
    internas = incoherencias_internas(orden)
    contraste = contrastar(incidencias, reales)
    sev = severidades(incidencias, reales)
    val = valores_correctos(incidencias, reales)
    comparables = campos_comparables(orden, cliente)
    casos = {}

    # 1 — discrepancias de severidad alta
    duras = [d for d in reales if d["severidad_esperada"] == "alta"]
    det = [d for d in contraste["detectadas"] if d["severidad_esperada"] == "alta"]
    omit = [d["etiqueta"] for d in contraste["omitidas"] if d["severidad_esperada"] == "alta"]
    casos[1] = _r(bool(duras) and len(det) == len(duras),
                  f"{len(det)} de {len(duras)} detectadas."
                  + (f" Omitidas: {', '.join(omit)}." if omit else ""),
                  omitir=not duras)

    # 2 — graduación de severidad
    malas = [s for s in sev if not s["correcta"]]
    casos[2] = _r(bool(sev) and not malas,
                  "; ".join(f"{s['etiqueta']}: {s['asignada'] or 'sin declarar'}"
                            + ("" if s["correcta"] else f" (esperada {s['esperada']})")
                            for s in sev) or "Sin severidades comparables.",
                  omitir=not sev)

    # 3 — precisión
    casos[3] = _r(not contraste["falsas"],
                  f"Precisión {contraste['precision']}%. "
                  + (f"Sin respaldo documental: "
                     f"{', '.join(ETIQUETAS.get(i.get('campo'), str(i.get('campo'))) for i in contraste['falsas'])}."
                     if contraste["falsas"] else "Todas las incidencias se sostienen en los documentos."),
                  omitir=not incidencias)

    # 4 — repetibilidad: requiere una segunda ejecución
    casos[4] = _r(False, "Requiere una segunda ejecución del módulo sobre el mismo pedido.",
                  omitir=True)

    # 5 — coherencia interna
    señaladas = any(i.get("interna") for i in incidencias)
    casos[5] = _r(not internas or señaladas,
                  (f"El evaluador detecta {len(internas)} incoherencia(s) interna(s): "
                   + "; ".join(f"cabecera {i['valor_cabecera']} frente a {i['valor_respaldo']} "
                               f"en {i['donde']}" for i in internas)
                   + ". El módulo no las señala."
                   if internas and not señaladas
                   else ("Incoherencia interna detectada también por el módulo."
                         if internas else "Sin incoherencias internas en el documento.")))

    # 6 — trazabilidad
    # Una incidencia interna afecta a un solo documento: exigirle dos referencias
    # sería un requisito imposible de cumplir.
    exigibles = [i for i in incidencias if not i.get("interna")]
    sin_fuente = [ETIQUETAS.get(i.get("campo"), str(i.get("campo")))
                  for i in exigibles if not i.get("cita_documentos")]
    casos[6] = _r(not sin_fuente,
                  f"{len(exigibles) - len(sin_fuente)} de {len(exigibles)} incidencias "
                  f"citan sus dos documentos."
                  + (f" Sin respaldo: {', '.join(sin_fuente)}." if sin_fuente else "")
                  + (f" {len(incidencias) - len(exigibles)} incidencia(s) interna(s) "
                     f"exentas por afectar a un solo documento."
                     if len(exigibles) < len(incidencias) else ""),
                  omitir=not exigibles)

    # 7 — dirección de la corrección
    declaradas = [i for i in incidencias if i.get("corregir")]
    mal = [ETIQUETAS.get(i["campo"], i["campo"]) for i in declaradas
           if i["corregir"] != "orden"]
    casos[7] = _r(not mal,
                  f"La corrección apunta a {nombre_orden} en todas las incidencias."
                  if not mal else
                  f"La corrección no apunta a la orden de fabricación en: {', '.join(mal)}.",
                  omitir=not declaradas)

    # 8 — cobertura de campos del módulo
    # Sólo es juzgable si existe discrepancia real en un campo distinto de los ya
    # probados. Si los demás campos coinciden, la ausencia de aviso no prueba nada.
    probados = {"cantidad", "gramaje_cubierta"}
    otros = [d for d in reales if d["campo"] not in probados]
    det_otros = [d for d in contraste["detectadas"] if d["campo"] not in probados]
    casos[8] = _r(bool(otros) and len(det_otros) == len(otros),
                  (f"Discrepancias en campos no probados: "
                   f"{', '.join(d['etiqueta'] for d in otros)}. "
                   f"Detectadas {len(det_otros)} de {len(otros)}."
                   if otros else
                   f"Campos comparados sin discrepancia: "
                   f"{', '.join(ETIQUETAS[k] for k in comparables)}. Se requiere un pedido "
                   f"con error introducido en uno de ellos."),
                  omitir=not otros)

    # 9 y 10 — requieren pedidos que no están disponibles
    casos[9] = _r(False, "Requiere un pedido sin documento de cliente o con PDF sin capa de texto.",
                  omitir=True)
    casos[10] = _r(False, "Requiere auditar la misma contradicción en el flujo de pedido y en el de chat.",
                   omitir=True)

    # --- valores mal citados: no altera el resultado de ningún caso, pero se informa
    mal_citados = [v for v in val if not v["correcto"]]

    return {"campos_orden": orden, "campos_cliente": cliente,
            "discrepancias_reales": reales, "incoherencias_internas": internas,
            "contraste": contraste, "severidades": sev, "valores": val,
            "mal_citados": mal_citados, "comparables": comparables, "casos": casos}


# ===========================================================================
# 7. EvaluationResult
# ===========================================================================

def resumen(casos):
    pasa = sum(1 for c in casos.values() if c["resultado"] == "pasa")
    no_pasa = sum(1 for c in casos.values() if c["resultado"] == "no_pasa")
    pend = sum(1 for c in casos.values() if c["resultado"] == "pendiente")
    return {"total": len(casos), "con_evidencia": pasa + no_pasa, "pasa": pasa,
            "no_pasa": no_pasa, "pendiente": pend,
            # El porcentaje se calcula sólo sobre lo verificado: contar los
            # pendientes como aprobados inflaría el resultado.
            "tasa": round(100 * pasa / (pasa + no_pasa), 1) if (pasa + no_pasa) else None}


ASPECTOS = {
    5: ("No se verifica la coherencia interna del documento auditado",
        "Comparar el documento consigo mismo antes de contrastarlo con fuentes externas. "
        "Un error de transcripción aislado en un campo de cabecera es detectable sin salir "
        "del fichero."),
    1: ("Se omiten discrepancias existentes entre los documentos",
        "Revisar la extracción de los campos omitidos: el evaluador los localiza en ambos "
        "documentos, luego la información estaba disponible."),
    2: ("La severidad asignada no corresponde a la esperada",
        "Fijar la clasificación de severidad por regla explícita en lugar de delegarla al "
        "modelo generativo."),
    3: ("Se emiten incidencias sin respaldo documental",
        "Exigir que toda incidencia cite los dos valores en conflicto y su localización "
        "antes de emitirse."),
    6: ("Hay incidencias que no citan sus documentos de origen",
        "Hacer obligatoria la referencia a ambos documentos en cada incidencia emitida."),
    7: ("La dirección de la corrección no es la esperada",
        "Declarar explícitamente que el documento de cliente es la fuente de verdad y la "
        "orden de fabricación el documento a corregir."),
    4: ("El determinismo del veredicto no está demostrado",
        "Ejecutar la auditoría dos veces sobre el mismo pedido sin modificar los documentos "
        "y comparar campos y severidad, no la redacción."),
    8: ("La cobertura de campos del módulo no está acotada",
        "Auditar un pedido con una discrepancia introducida en un campo distinto de cantidad "
        "y gramaje, y declarar qué campos entran en la comparación."),
    9: ("No consta distinción entre ausencia de incidencias e imposibilidad de comprobar",
        "Diferenciar ambas situaciones en la salida: una salida vacía por documento ilegible "
        "llegaría a validación humana como señal de pedido correcto."),
    10: ("El alcance declarado de cada flujo no está verificado",
         "Comprobar que el flujo de pedido se limita a los documentos de ese pedido y el de "
         "chat abarca toda la base documental."),
}

ETIQUETA_CASO = {"no_pasa": "caso fallido", "pendiente": "caso pendiente",
                 "pasa": "caso superado"}


def evaluation_result(ev, pedido, fecha=None):
    casos = ev["casos"]
    r = resumen(casos)

    if r["con_evidencia"] == 0:
        val = ("No es posible emitir valoración: ninguno de los casos diseñados ha podido "
               "ejecutarse con los documentos aportados.")
    else:
        c = ev["contraste"]
        val = (f"Sobre el pedido {pedido}, el módulo supera {r['pasa']} de los "
               f"{r['con_evidencia']} casos verificados ({r['tasa']}%). ")
        if c["exhaustividad"] is not None:
            val += (f"Localiza el {c['exhaustividad']}% de las discrepancias que el evaluador "
                    f"encuentra de forma independiente en los documentos")
            val += (f", y el {c['precision']}% de las incidencias que emite se sostienen "
                    f"documentalmente. " if c["precision"] is not None else ". ")
        val += ("La evaluación se apoya en salidas reales del módulo contrastadas contra los "
                "documentos de origen, no en casos construidos por el evaluador. ")
        if r["no_pasa"]:
            val += (f"Se ha identificado {'un fallo' if r['no_pasa'] == 1 else str(r['no_pasa']) + ' fallos'} "
                    f"con evidencia directa. ")
        if r["pendiente"]:
            val += (f"{'Queda un caso' if r['pendiente'] == 1 else 'Quedan ' + str(r['pendiente']) + ' casos'} "
                    f"sin ejecutar por falta de datos, cuyo resultado no se presume en ningún sentido.")

    orden = {"no_pasa": 0, "pendiente": 1, "pasa": 2}
    aspectos = []
    for n, c in sorted(casos.items(), key=lambda kv: (orden[kv[1]["resultado"]], kv[0])):
        if c["resultado"] == "pasa" or n not in ASPECTOS:
            continue
        titulo, correccion = ASPECTOS[n]
        aspectos.append({"titulo": titulo, "caso": n, "nombre_caso": CASOS[n],
                         "estado": c["resultado"], "detalle": c["observacion"],
                         "correccion": correccion})

    return {"modulo": "Auditoría de pedidos", "responsable": "Juan Salas",
            "conexion": "Carga de contradicciones hacia validación humana (Mencía Viñuelas)",
            "pedido": pedido, "fecha": (fecha or date.today()).strftime("%d/%m/%Y"),
            "resumen": r, "valoracion": val, "aspectos": aspectos}


def a_markdown(er, ev):
    r, c = er["resumen"], ev["contraste"]
    L = ["# EvaluationResult", "",
         f"## {er['modulo']} — {er['responsable']}", "",
         f"**Conexión evaluada:** {er['conexion']}",
         f"**Pedido evaluado:** {er['pedido']}",
         f"**Fecha:** {er['fecha']}",
         "**Origen de los datos:** salida real del módulo, contrastada contra los "
         "documentos de origen", "",
         "| | |", "|---|---|",
         f"| Casos diseñados | {r['total']} |",
         f"| Casos con evidencia | {r['con_evidencia']} |",
         f"| Superados | {r['pasa']} |",
         f"| Fallidos | {r['no_pasa']} |",
         f"| Pendientes de datos | {r['pendiente']} |",
         f"| Exhaustividad | {c['exhaustividad']}% |",
         f"| Precisión | {c['precision']}% |", "",
         "## Valoración", "", er["valoracion"], ""]

    if ev["discrepancias_reales"]:
        L += ["## Discrepancias calculadas por el evaluador", "",
              "| Campo | Cliente | Orden de fabricación | Severidad esperada | Detectada |",
              "|---|---|---|---|---|"]
        for d in ev["discrepancias_reales"]:
            det = "Sí" if d in c["detectadas"] else "No"
            L.append(f"| {d['etiqueta']} | {d['valor_cliente']} | {d['valor_orden']} | "
                     f"{d['severidad_esperada']} | {det} |")
        L.append("")

    if ev["incoherencias_internas"]:
        L += ["## Incoherencias internas de la orden de fabricación", "",
              "| Campo | Cabecera | Valor de respaldo | Procedencia |", "|---|---|---|---|"]
        for i in ev["incoherencias_internas"]:
            L.append(f"| {i['etiqueta']} | {i['valor_cabecera']} | {i['valor_respaldo']} | "
                     f"{i['donde']} |")
        L.append("")

    L += ["## Resultado caso a caso", "", "| # | Caso | Resultado | Observación |",
          "|---|---|---|---|"]
    for n, caso in sorted(ev["casos"].items()):
        L.append(f"| {n} | {CASOS[n]} | {caso['resultado'].replace('_', ' ')} | "
                 f"{caso['observacion']} |")
    L.append("")

    if er["aspectos"]:
        L += ["## Aspectos a mejorar", ""]
        for i, a in enumerate(er["aspectos"], 1):
            L += [f"### {i}. {a['titulo']}",
                  f"*Caso {a['caso']} · {a['nombre_caso']} · {ETIQUETA_CASO[a['estado']]}*", "",
                  a["detalle"], "",
                  f"**Corrección propuesta:** {a['correccion']}", ""]

    L += ["---", "",
          "*Cada aspecto a mejorar queda anclado al caso de la batería que lo evidencia. "
          "Los casos pendientes describen comprobaciones diseñadas y no ejecutadas: no "
          "computan a favor ni en contra del módulo evaluado.*"]
    return "\n".join(L)
