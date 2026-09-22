[English](README.md) | [Português](README.pt-BR.md) | [Español](README.es.md)

# Career Agent
Encontre vagas, entenda por que combinam com sua busca e prepare currículos apoiados em evidências no seu computador.

![Discover do Career Agent com vagas fictícias](docs/assets/readme/discover.png)

## Status do projeto
**Alpha v0.1.0-alpha.2**, com **Resume Tailor Beta** incluído. É um aplicativo local funcional, ainda em desenvolvimento. Search Fit explica suas preferências de busca; não representa probabilidade de contratação.

## Instalação mais fácil: Windows
1. Abra [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases).
2. Baixe `Career-Agent-v0.1.0-alpha.2-Windows.zip`.
3. Clique com o botão direito no ZIP, escolha **Extrair Tudo** e abra a pasta extraída.
4. Dê dois cliques em **Start-Career-Agent.cmd**. Deixe a janela aberta.
5. Aguarde a preparação. O navegador abrirá automaticamente.

Você não precisa instalar Python nem Node. O launcher baixa o uv quando necessário; o uv instala Python 3.12 e as dependências nas versões registradas. A primeira instalação precisa de internet. Use uma pasta em que você possa gravar arquivos, fora de Arquivos de Programas. [Ajuda para começar](FIRST_RUN.md).

## Como abrir novamente
Dê dois cliques no mesmo launcher, na mesma pasta. Seus dados ficam ali. Pressione **Ctrl+C** na janela para encerrar os dois aplicativos. Faça backup antes de mudar de versão.

## O que ele faz
- Encontra vagas em fontes compatíveis ou permite importar uma descrição.
- Verifica elegibilidade de contratação separadamente das preferências da busca.
- Explica o Search Fit, mostrando razões e informações ausentes.
- Organiza evidências da sua trajetória e acompanha candidaturas que você decide enviar.

## Resume Tailor Beta
Abra **Resume Tailor Beta** no menu lateral. Dentro de uma vaga, escolha **Copy job description**, abra o Tailor e cole em **Tailor resume**.

Crie um perfil de candidato, adicione currículo-base e fontes, revise evidências, analise a vaga, examine correspondências e lacunas, gere, edite, valide e exporte. Markdown e Word funcionam sem IA. PDF requer Microsoft Word ou LibreOffice instalado no computador; quando não há nenhum deles, o aplicativo explica a indisponibilidade.

Os módulos guardam evidências separadamente. Perfil e evidências não são sincronizados automaticamente. **Search Fit e Tailor Match respondem a perguntas diferentes.** Texto gerado nunca vira Career Evidence confirmada. Experiência sem respaldo não deve virar afirmação no currículo. Consulte as [garantias de honestidade](docs/ARCHITECTURE.md).

## Tour visual
Todas as imagens usam dados fictícios da demonstração.

| Encontrar e entender | Preparar e revisar |
|---|---|
| ![Por que esta vaga combina](docs/assets/readme/why.png) | ![Resume Tailor Beta](docs/assets/readme/tailor.png) |
| ![Primeiro uso](docs/assets/readme/first-run.png) | Análise da vaga → evidências → estratégia → geração → validação → edição → exportação |

## Privacidade
Configurações, vagas, notas e evidências ficam armazenadas localmente. O Tailor mantém os documentos enviados como fontes; o Career Agent extrai texto do CV sem guardar o arquivo enviado. Provedores opcionais de IA podem receber conteúdo quando você os configura e utiliza. Coleta de vagas e links de empregadores também usam internet. Não há telemetria implementada. Leia o [modelo de privacidade](docs/PRIVACY.md), incluindo backups e área de transferência.

## O que ele deliberadamente não faz
Não envia candidaturas automaticamente, não promete entrevistas, não interpreta trabalho remoto como elegibilidade mundial, não esconde lacunas nem trata texto de IA como experiência confirmada.

## Instalação pelo terminal
**Windows: abra Iniciar, digite PowerShell e abra Windows PowerShell.** No macOS, abra Aplicativos → Utilitários → Terminal. No Linux, abra o aplicativo Terminal.

Instale o [uv](https://docs.astral.sh/uv/getting-started/installation/), baixe e extraia o ZIP do código-fonte. Digite `cd` seguido do caminho da pasta entre aspas. Execute:

```powershell
uv sync --locked --python 3.12
uv run python scripts/launch.py
```

Não é necessário instalar Python manualmente. Windows é a plataforma testada para este release. Para outras portas: `uv run python scripts/launch.py --port 8875` (o Tailor usará 8876).

## Demo
Dê dois cliques em **Start-Demo.cmd**, ou execute `uv run python scripts/launch.py --demo`. A demonstração cria vagas inventadas e Alex Morgan, um candidato fictício, em armazenamento separado. Encerre o modo pessoal antes de abrir a demo nas mesmas portas. A demo nunca usa provedor de IA.

## Desenvolvimento e testes
Veja os comandos em [CONTRIBUTING.md](CONTRIBUTING.md) e os resultados medidos em [validação do release](docs/VALIDATION.md). A interface compilada do Tailor está incluída; Node só é necessário para reconstruí-la. [Arquitetura](docs/ARCHITECTURE.md), [permissões das fontes](docs/SOURCES.md) e [auditoria pública](docs/PUBLIC_AUDIT.md) explicam as decisões de engenharia.

## Limitações
Alpha: fontes podem mudar, os leitores de idioma são incompletos e a elegibilidade pode continuar desconhecida. Beta: você continua responsável pela revisão das evidências; os módulos não compartilham perfis. PDF e contagem real de páginas dependem de um renderizador local. O app atende uma pessoa por vez, sem acesso remoto nem sincronização em nuvem. A primeira instalação não funciona offline.

## Licença e avisos de terceiros
Career Agent usa [MIT](LICENSE). Resume Tailor, em `companion/resume-tailor`, usa [Apache-2.0](companion/resume-tailor/LICENSE). As fontes mantêm suas licenças OFL. Veja [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para os limites entre componentes, dependências e atribuições.
