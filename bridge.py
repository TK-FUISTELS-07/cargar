import asyncio
import aiohttp
import re
import ujson
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
import uvicorn

# Configuración y Regex Pre-compiladas (Ahorro de CPU)
RE_TOKEN = re.compile(r'["\'](eyJ[a-zA-Z0-9._-]+)["\']')
RE_ID = re.compile(r'data-id=["\'](tt\d+|[a-zA-Z0-9]+)["\']')

class AlphaBridge:
    def __init__(self):
        self.session = None
        self.connector = None

    async def _init_session(self):
        """Inicialización segura del motor de red dentro del event loop."""
        if self.session is None or self.session.closed:
            # TCPConnector optimizado para no saturar RAM
            self.connector = aiohttp.TCPConnector(
                limit=100,           # Máximo de conexiones simultáneas
                ttl_dns_cache=600,    # Cache de DNS para ahorrar CPU
                use_dns_cache=True,
                ssl=False             # Acelera peticiones si no validamos SSL (opcional)
            )
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                json_serialize=ujson.dumps,
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self.session

    async def extract_logic(self, url, headers):
        """Lógica de extracción con reintentos y manejo de errores."""
        session = await self._init_session()
        
        # Intentar hasta 2 veces por URL si hay micro-cortes
        for intento in range(2):
            try:
                # 1. Obtener la página principal
                async with session.get(url, headers=headers, allow_redirects=True) as r:
                    if r.status != 200:
                        continue
                    html = await r.text()

                # 2. Identificar el ID (tt o slug)
                v_id = RE_ID.search(html)
                v_id = v_id.group(1) if v_id else None
                if not v_id and "/f/" in html:
                    try: v_id = html.split('/f/')[1].split('/')[0].split('"')[0]
                    except: pass

                if not v_id:
                    return f"enlaces reales de: {url}\n [ID no encontrado]\n\n"

                # 3. Obtener Tokens del Iframe
                iframe_url = f"https://embed69.org/f/{v_id}/"
                async with session.get(iframe_url, headers=headers) as ir:
                    iframe_html = await ir.text()
                    tokens = RE_TOKEN.findall(iframe_html)
                    
                    if not tokens:
                        return f"enlaces reales de: {url}\n [Tokens no hallados]\n\n"

                # 4. Decodificación Final
                api_headers = headers.copy()
                api_headers["Referer"] = iframe_url
                async with session.post(
                    "https://embed69.org/api/decrypt",
                    json={"links": tokens},
                    headers=api_headers
                ) as dr:
                    if dr.status != 200: return f"enlaces reales de: {url}\n [Error API Decrypt]\n\n"
                    
                    data = await dr.json()
                    links = [it['link'] for it in data.get('links', []) if 'link' in it]
                    
                    # Construcción eficiente del string
                    res = [f"enlaces reales de: {url}"]
                    res.extend([f" {l}" for l in links])
                    return "\n".join(res) + "\n\n"

            except Exception:
                if intento == 1: # Si falló el último intento
                    return f"enlaces reales de: {url}\n [Fallo de conexión persistente]\n\n"
                await asyncio.sleep(0.5) # Esperar un poco antes de reintentar
        
        return f"enlaces reales de: {url}\n [Error desconocido]\n\n"

# Instancia única para mantener la persistencia de conexiones
bridge = AlphaBridge()

async def extract_api(request):
    """Handler principal de la API."""
    try:
        # Validación de entrada
        body = await request.json()
        headers = body.get("headers", {})
        raw_urls = body.get("urls", [])
        
        # Límite estricto de 20 URLs para proteger los 2GB de RAM
        urls = raw_urls[:20]
        
        if not urls:
            return Response("Error: Lista de URLs vacía.", status_code=400)

        # Lanzar todas las peticiones en paralelo
        tasks = [bridge.extract_logic(u, headers) for u in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Limpiar resultados (evitar que excepciones rompan el join)
        clean_results = [r if isinstance(r, str) else "[Error de Tarea]\n\n" for r in results]
        
        return Response("".join(clean_results), media_type="text/plain")

    except ujson.JSONDecodeError:
        return Response("Error: JSON mal formado.", status_code=400)
    except Exception as e:
        return Response(f"Error Interno: {str(e)}", status_code=500)

# Configuración de Starlette (Ligero y rápido)
routes = [Route('/extract', extract_api, methods=['POST'])]
middleware = [Middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['POST'], allow_headers=['*'])]
app = Starlette(routes=routes, middleware=middleware)

if __name__ == "__main__":
    # Configuración de producción para recursos limitados
    uvicorn.run(
        "bridge_final:app", # Import string para permitir hot-reload si fuera necesario
        host="0.0.0.0", 
        port=5000, 
        workers=1,           # Un solo proceso maneja miles de peticiones gracias a asincronía
        loop="uvloop",       # Máxima velocidad en Linux
        http="httptools",    # Parser de alta velocidad
        limit_concurrency=1000, # Cola de usuarios máxima
        timeout_keep_alive=5
    )
