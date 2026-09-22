[English](README.md) | [Português](README.pt-BR.md) | [Español](README.es.md)

# Career Agent
Encuentra ofertas, entiende por qué encajan con tu búsqueda y prepara currículums respaldados por evidencias en tu ordenador.

![Discover de Career Agent con ofertas ficticias](docs/assets/readme/discover.png)

## Estado del proyecto
**Alpha v0.1.0-alpha.2**, con **Resume Tailor Beta** incluido. Es una aplicación local funcional, todavía en desarrollo. Search Fit explica tus preferencias de búsqueda; no indica la probabilidad de contratación.

## Instalación más sencilla: Windows
1. Abre [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases).
2. Descarga `Career-Agent-v0.1.0-alpha.2-Windows.zip`.
3. Haz clic derecho en el ZIP, elige **Extraer todo** y abre la carpeta extraída.
4. Haz doble clic en **Start-Career-Agent.cmd**. Deja abierta esa ventana.
5. Espera a que termine la preparación. El navegador se abrirá automáticamente.

No necesitas instalar Python ni Node. El lanzador descarga uv si hace falta; uv instala Python 3.12 y las dependencias en las versiones registradas. La primera instalación necesita internet. Usa una carpeta donde puedas guardar archivos, fuera de Archivos de programa. [Ayuda para empezar](FIRST_RUN.md).

## Cómo volver a abrirlo
Haz doble clic en el mismo lanzador, en la misma carpeta. Tus datos permanecen allí. Pulsa **Ctrl+C** en su ventana para cerrar las dos aplicaciones. Haz una copia de seguridad antes de cambiar de versión.

## Qué hace
- Busca ofertas en fuentes compatibles o permite importar una descripción.
- Comprueba la elegibilidad de contratación por separado de tus preferencias.
- Explica Search Fit, mostrando los motivos y la información que falta.
- Organiza las evidencias de tu trayectoria y sigue las candidaturas que decides enviar.

## Resume Tailor Beta
Abre **Resume Tailor Beta** en el menú lateral. Desde una oferta, elige **Copy job description**, abre Tailor y pega el texto en **Tailor resume**.

Crea un perfil de candidato, añade un currículum base y fuentes, revisa las evidencias, analiza la oferta, examina coincidencias y carencias, genera, edita, valida y exporta. Markdown y Word funcionan sin IA. PDF necesita Microsoft Word o LibreOffice instalado localmente; si no está disponible ninguno, la aplicación lo explica.

Los módulos almacenan evidencias por separado. No sincronizan perfiles ni evidencias automáticamente. **Search Fit y Tailor Match responden a preguntas distintas.** El texto generado nunca se convierte en Career Evidence confirmada. La experiencia sin respaldo no debe aparecer como afirmación en el currículum. Consulta las [garantías de honestidad](docs/ARCHITECTURE.md).

## Recorrido visual
Todas las imágenes utilizan datos ficticios de demostración.

| Encontrar y entender | Preparar y revisar |
|---|---|
| ![Por qué encaja esta oferta](docs/assets/readme/why.png) | ![Resume Tailor Beta](docs/assets/readme/tailor.png) |
| ![Primer uso](docs/assets/readme/first-run.png) | Análisis de la oferta → evidencias → estrategia → generación → validación → edición → exportación |

## Privacidad
Configuración, ofertas, notas y evidencias se guardan localmente. Tailor conserva los documentos añadidos como fuentes; Career Agent extrae el texto del CV sin guardar el archivo subido. Los proveedores opcionales de IA pueden recibir contenido cuando los configuras y utilizas. La recopilación de ofertas y los enlaces a empleadores también usan internet. No hay telemetría implementada. Lee el [modelo de privacidad](docs/PRIVACY.md), incluidas las copias de seguridad y el portapapeles.

## Qué no hace deliberadamente
No envía candidaturas automáticamente, no promete entrevistas, no interpreta trabajo remoto como elegibilidad mundial, no oculta carencias ni trata texto de IA como experiencia confirmada.

## Instalación desde el terminal
**Windows: abre Inicio, escribe PowerShell y abre Windows PowerShell.** En macOS, abre Aplicaciones → Utilidades → Terminal. En Linux, abre tu aplicación Terminal.

Instala [uv](https://docs.astral.sh/uv/getting-started/installation/), descarga y extrae el ZIP del código fuente. Escribe `cd` seguido de la ruta de la carpeta entre comillas. Ejecuta:

```powershell
uv sync --locked --python 3.12
uv run python scripts/launch.py
```

No hace falta instalar Python manualmente. Windows es la plataforma probada para este release. Para usar otros puertos: `uv run python scripts/launch.py --port 8875` (Tailor usará 8876).

## Demo
Haz doble clic en **Start-Demo.cmd**, o ejecuta `uv run python scripts/launch.py --demo`. Crea ofertas inventadas y Alex Morgan, un candidato ficticio, en almacenamiento separado. Cierra el modo personal antes de abrir la demo en los mismos puertos. La demo nunca utiliza un proveedor de IA.

## Desarrollo y pruebas
Consulta los comandos en [CONTRIBUTING.md](CONTRIBUTING.md) y los resultados medidos en [validación del release](docs/VALIDATION.md). La interfaz compilada de Tailor está incluida; Node solo es necesario para reconstruirla. [Arquitectura](docs/ARCHITECTURE.md), [permisos de fuentes](docs/SOURCES.md) y [auditoría pública](docs/PUBLIC_AUDIT.md) explican las decisiones de ingeniería.

## Limitaciones
Alpha: las fuentes pueden cambiar, los lectores de idiomas son incompletos y la elegibilidad puede seguir siendo desconocida. Beta: sigues siendo responsable de revisar las evidencias; los módulos no comparten perfiles. PDF y el recuento real de páginas dependen de un renderizador local. La aplicación es para una sola persona, sin acceso remoto ni sincronización en la nube. La primera instalación no funciona sin internet.

## Licencia y avisos de terceros
Career Agent utiliza [MIT](LICENSE). Resume Tailor, en `companion/resume-tailor`, utiliza [Apache-2.0](companion/resume-tailor/LICENSE). Las fuentes conservan sus licencias OFL. Consulta [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para los límites entre componentes, dependencias y atribuciones.
