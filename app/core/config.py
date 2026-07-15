from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str | None = None
    secret_key: str = "development-only-secret-key-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # ── SSO via reverse-proxy (oauth2-proxy + Microsoft Entra) ────────
    # Quando True, GET /api/v1/auth/sso/session confia no header injetado
    # pelo proxy (X-Auth-Request-Email) pra autenticar/provisionar o usuário.
    # DEVE ser True SÓ em produção, com o app ATRÁS do proxy (senão o header
    # pode ser forjado). Default False (dev local sem proxy).
    sso_header_auth_enabled: bool = False
    sso_email_header: str = "x-auth-request-email"
    sso_name_header: str = "x-auth-request-user"
    # Header com o ID token (JWT) injetado pelo oauth2-proxy quando
    # OAUTH2_PROXY_SET_AUTHORIZATION_HEADER=true. Usado só pra extrair o claim
    # `name` (nome completo do Entra) — o oauth2-proxy não expõe o nome nos
    # X-Auth-Request-*. Decodificado SEM verificar assinatura (vem do proxy
    # confiável, igual aos demais headers). Ver app/api/v1/endpoints/auth.py.
    sso_id_token_header: str = "authorization"
    # URL de verificação do oauth2-proxy (/oauth2/auth). Quando setada, o
    # /auth/sso/session valida a sessão SSO chamando esta URL server-side com o
    # cookie do usuário — substitui o forward-auth do Traefik (que o Coolify
    # aplicava no domínio inteiro). Prod: https://auth.dunatecnologia.com/oauth2/auth
    sso_validate_url: str = ""

    cors_allowed_origins: str = "http://localhost:5173,http://localhost:8080"
    spreadsheet_max_size_mb: int = 10
    spreadsheet_allowed_content_types: str = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
        "application/octet-stream"
    )
    batch_worker_enabled: bool = True
    batch_worker_poll_interval_seconds: int = 5
    batch_worker_lease_seconds: int = 300

    legal_one_base_url: str | None = None
    legal_one_client_id: str | None = None
    legal_one_client_secret: str | None = None
    legal_one_position_fix_status_file: str | None = None
    legal_one_web_username: str | None = None
    legal_one_web_password: str | None = None
    legal_one_web_key_label: str | None = None
    publication_treatment_output_dir: str | None = None
    publication_treatment_runner_script: str | None = None
    publication_treatment_batch_size: int = 20
    publication_treatment_pause_seconds: int = 5
    publication_treatment_max_attempts: int = 3
    publication_treatment_monitor_poll_seconds: int = 5
    # Autorun recorrente do Tratamento Web: dispara start_run no cron abaixo
    # (timezone America/Sao_Paulo). Default: 01h, 04h, 12h e 22h.
    publication_treatment_autorun_enabled: bool = True
    publication_treatment_autorun_cron: str = "0 1,4,12,22 * * *"
    # Run ativo sem heartbeat (status.json parado) ha mais que isso e
    # considerado zumbi e marcado como FALHA pra destravar o proximo ciclo.
    publication_treatment_stale_run_minutes: int = 30

    smtp_server: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None
    email_to: str | None = None
    mail_mailer: str | None = None
    mail_host: str | None = None
    mail_port: int | None = None
    mail_username: str | None = None
    mail_password: str | None = None
    mail_encryption: str | None = None
    mail_from_address: str | None = None
    mail_from_name: str | None = None
    mail_to: str | None = None
    system_name: str | None = None
    app_name: str | None = None

    datajud_base_url: str = "https://api-publica.datajud.cnj.jus.br"
    datajud_api_key: str | None = None
    datajud_timeout_seconds: int = 30
    datajud_default_page_size: int = 100

    comunica_base_url: str = "https://comunicaapi.pje.jus.br"
    comunica_timeout_seconds: int = 30
    djen_default_meio: str = "D"

    process_monitoring_idle_window_days: int = 15
    process_monitoring_recency_window_days: int = 10

    # ── Publication Capture (Legal One /Updates) ──────────────────────
    # Quando um escritório é capturado pela primeira vez (nenhum cursor
    # prévio), a rodagem inicial olha para trás este número de dias.
    publication_initial_lookback_days: int = 3
    # Overlap defensivo aplicado em todas as rodagens seguintes
    # (date_from = last_success − overlap). Mantém a janela fechada
    # mesmo se houver atraso de processamento/indexação no L1.
    publication_overlap_hours: int = 1
    # Campo do Legal One usado para filtrar a busca:
    # "creationDate" = data em que o L1 disponibilizou a publicação (recomendado)
    # "date"         = data efetiva da publicação no diário (pode perder entradas tardias)
    publication_capture_date_field: str = "creationDate"
    # Modo batch do scheduler: 1 chamada L1 + fan-out por escritório.
    # Quando False (legado), itera office por office fazendo 1 fetch L1
    # por escritório — desperdiça paginação (L1 devolve tudo do período
    # independente do filtro de escritório, que é client-side) e satura
    # rate limit em rodadas multi-banco. Default ON desde 2026-05-07;
    # se voltar a falhar, setar False no Coolify pra rollback rápido.
    publication_scheduler_batch_mode: bool = True

    # Classifier Engine
    anthropic_api_key: str | None = None
    classifier_model: str = "claude-haiku-4-5-20251001"
    classifier_max_concurrent: int = 5
    classifier_max_tokens: int = 4096

    # ── Prazos Iniciais ───────────────────────────────────────────────
    # Chave(s) que autenticam a automação externa no endpoint de intake.
    # Aceita múltiplas chaves separadas por vírgula (rotação sem downtime).
    prazos_iniciais_api_key: str | None = None
    # Pasta raiz (dentro do volume persistente) onde os PDFs da habilitação
    # são guardados até o upload no GED do L1.
    prazos_iniciais_storage_path: str = "/app/data/prazos_iniciais"
    # Limite por PDF — compartilhado entre PI (habilitacao) e
    # Classificador (processo completo). PI usa <5MB tipicamente; o
    # Classificador roda pikepdf compress no intake (reduz 15-40%),
    # entao mesmo PDFs de 200MB no upload ficam menores apos persistir.
    # Override via env var PRAZOS_INICIAIS_MAX_PDF_MB se precisar.
    prazos_iniciais_max_pdf_mb: int = 200
    # Upload manual via UI (USER_UPLOAD) — processos na íntegra costumam
    # ser bem maiores que o PDF de habilitação, então temos um limite
    # próprio. PDF do processo é descartado após extração ok pra não
    # encher disco.
    prazos_iniciais_max_upload_pdf_mb: int = 100
    # Quantos dias manter o PDF local após confirmação de upload no GED.
    prazos_iniciais_retention_days: int = 7
    # Parâmetros do agregador (janela antes de submeter batch pra Anthropic).
    prazos_iniciais_batch_window_seconds: int = 600
    prazos_iniciais_batch_min_size: int = 5
    prazos_iniciais_batch_max_size: int = 100
    # typeId usado no L1 para o documento de habilitação (Documento/Habilitação).
    # Formato literal "type_N" descoberto em 2026-05-04 (type_48 = Habilitação).
    prazos_iniciais_ged_type_id: str = "type_48"
    # Modelo Anthropic usado na classificação (Sonnet — mais sensível).
    prazos_iniciais_classifier_model: str = "claude-sonnet-4-6"
    prazos_iniciais_classifier_max_tokens: int = 4096

    # ── Análise Recursal (dentro de Prazos Processuais) ───────────────
    # 1 chamada Sonnet por processo, via Batches (custo ~R$0,17/processo).
    # Reusa o extractor mecânico de Prazos Iniciais (capa+integra).
    recursal_classifier_model: str = "claude-sonnet-4-6"
    recursal_classifier_max_tokens: int = 4096
    # Worker de background: auto-submete pendentes e auto-aplica batches
    # prontos (fire-and-forget; não depende do operador ficar na tela).
    recursal_auto_worker_enabled: bool = True
    recursal_auto_worker_interval_seconds: int = 180
    # OCR dos documentos-imagem (Tesseract). Só roda nas páginas sem texto.
    # Capado pra não estourar o tempo de upload nem o custo de token.
    recursal_ocr_enabled: bool = True
    recursal_ocr_max_pages: int = 15
    recursal_ocr_max_chars: int = 18_000

    # ─── Classificador — motor dormente (cla003) ───────────────────────
    # API keys aceitas no endpoint publico POST /classificador/intake/pdf
    # (separadas por virgula no .env). Vazio = endpoint desativado.
    classificador_api_key: str | None = None
    # Tamanho do batch que o worker dormente agrupa antes de criar lote.
    classificador_batch_size: int = 50
    # Timeout em minutos — se passar disso sem atingir batch_size, cria lote
    # mesmo assim com o que tiver (evita PDFs ficarem presos na fila).
    classificador_batch_timeout_minutes: int = 30
    # Worker desligado por default em dev pra evitar surpresas.
    classificador_pending_worker_enabled: bool = False
    # Intervalo entre ticks do worker (segundos).
    classificador_pending_worker_interval_seconds: int = 60
    # Concorrencia interna do worker: quantos PDFs processa em paralelo
    # dentro de UM mesmo lote. Cada thread = 1 PDF (extract + compress
    # + save). Padrao 4 e' conservador pra nao estourar CPU em PDFs
    # grandes; pode subir pra 8-12 se tiver mais cpu disponivel.
    # IMPORTANTE: cada thread cria sua propria SessionLocal — DB nao
    # bottleneca, mas memoria sim (cada PDF de 30MB ocupa RAM).
    classificador_pending_worker_concurrency: int = 4
    # Auto-classify: se True, dispara classify do lote logo apos criar.
    classificador_pending_auto_classify: bool = True

    # ─── Classificador — compressao de PDF ─────────────────────────────
    # Comprime PDFs antes de salvar via pikepdf (streams + dedup).
    # Mantém 100% do texto. Reducao tipica 15-40% pra PDFs nativos.
    classificador_compression_enabled: bool = True
    # Skip arquivos abaixo desse limite (em KB) — nao vale a pena
    # processar PDFs pequenos.
    classificador_compression_min_kb: int = 2048  # 2MB
    # Skip arquivos ACIMA desse limite — pikepdf demora 20-40s pra
    # comprimir PDFs de 30+MB, e como o cleanup imediato deleta o PDF
    # logo apos extracao, nao vale a pena pagar esse custo. Salva 20-40s
    # por PDF grande no quick-pdf.
    classificador_compression_max_kb: int = 20480  # 20MB
    # Se compressao piorar OU pikepdf der erro, volta ao bytes originais
    # silenciosamente. NAO bloqueia o intake.

    # ─── Classificador — retencao de PDFs ──────────────────────────────
    # Default: descarta o PDF imediatamente apos extracao mecanica OK.
    # Capa_json + integra_json + classificacao_response_json ficam no DB.
    # Pdf_sha256 + pdf_bytes mantem-se como auditoria. Pra reclassificar
    # ou reprocessar, basta o integra_json (nao precisa do PDF binario).
    # Mude pra True se quiser manter o binario por mais tempo (mais disco).
    classificador_keep_pdf_after_success: bool = False

    # ─── Classificador — webhook callback ──────────────────────────────
    # URL pra notificar quando lote vira CLASSIFICADO (robo de entrega
    # quer saber quando o resultado tá pronto). Vazio = desativa.
    classificador_webhook_url: str | None = None
    # Secret pra assinar payload (HMAC-SHA256 em header X-Classificador-Signature).
    # Cliente valida pra rejeitar webhooks falsos.
    classificador_webhook_secret: str | None = None
    # Retries em caso de erro HTTP. Timeout: 5s/30s/2min.
    classificador_webhook_max_retries: int = 3
    # Timeout HTTP por tentativa (segundos).
    classificador_webhook_timeout_seconds: int = 10
    # Worker periódico: agrega intakes PRONTO_PARA_CLASSIFICAR e dispara
    # batch + faz polling/apply dos batches pendentes.
    # Desligado por padrão em dev pra evitar gasto involuntário com Anthropic.
    prazos_iniciais_auto_classification_enabled: bool = False
    # Intervalo entre execuções do worker (segundos).
    prazos_iniciais_auto_classification_interval_seconds: int = 300
    # Fila de cancelamento da task legada "Agendar Prazos".
    # 2026-05-06: ligado por default. Antes era manual (operador clicava
    # "Processar" no Tratamento Web), o que exigia babá no painel. Agora
    # roda sozinho: fluxo "agendar e esquecer". Items que nao tem task L1
    # (USER_UPLOAD ou backlog) caem em task_not_found e viram COMPLETED
    # silencioso via QUEUE_NOOP_REASONS — nao empacam mais a fila.
    prazos_iniciais_legacy_task_cancellation_enabled: bool = True
    prazos_iniciais_legacy_task_cancellation_interval_seconds: int = 60
    # Batch=10 e' a vazao validada pre-2026-05-06. Tentei 25 nesse dia
    # combinado com rate_limit=0.5s e o L1 comecou a NAO PERSISTIR saves
    # (form retornava ok mas /Tasks/{id} mostrava statusId=0 — verificado
    # pela API L1, que e' fonte da verdade desde 5e94fdb). Reverti pra
    # nao estressar o L1 web.
    prazos_iniciais_legacy_task_cancellation_batch_size: int = 10
    # Rate limit entre items consecutivos. 2.0s e' o valor que o L1 web
    # absorve sem rejeitar saves silenciosamente. Tentei 0.5s no mesmo
    # dia (2026-05-06) e o save passou a falhar em massa — reverti.
    # Operador pode customizar via env se quiser experimentar.
    prazos_iniciais_legacy_task_cancel_rate_limit_seconds: float = 2.0
    # Cap de tentativas por item: depois disso, o worker periodico SKIPA
    # o item — ele ainda fica visivel em FAILED na UI pra reprocesso
    # manual ("Reprocessar" reseta attempt_count e devolve pra PENDING),
    # mas nao gasta mais slots de tick. Sem isso, items que falham
    # consistentemente (ex.: layout_drift permanente) ficam no loop
    # eterno e travam a fila pros novos.
    prazos_iniciais_legacy_task_max_attempts: int = 5
    # Circuit breaker: após N falhas de infraestrutura (auth/timeout/exception)
    # consecutivas, o worker pula ticks por cooldown_minutes minutos. Sucesso
    # zera o contador; falhas de negócio (task_not_found, layout_drift) não
    # contam porque sinalizam problemas de dado, não de conexão.
    prazos_iniciais_legacy_task_circuit_breaker_threshold: int = 3
    prazos_iniciais_legacy_task_circuit_breaker_cooldown_minutes: int = 10
    # Threshold pra detectar items "zumbis": entraram em PROCESSANDO mas nunca
    # sairam (worker crashou no meio do tick, RPA travou, container reiniciou
    # com item em flight). Apos N minutos no estado PROCESSANDO, o tick do
    # worker devolve pra PENDENTE e incrementa attempt_count. Sem isso, items
    # zumbis ficam pra sempre como PROCESSANDO, inflam o painel e nao saem da
    # fila. Default 5 minutos (rate_limit=2s + RPA tipico ~20-30s, entao 5min
    # cobre RPAs lentos sem travar zumbis reais).
    prazos_iniciais_legacy_task_zombie_threshold_minutes: int = 5
    # TTL do cookie .ASPXAUTH cacheado em arquivo (volume /app/data,
    # compartilhado entre os 4 workers Uvicorn via filelock). Cookie real
    # do L1 dura ~horas; usar janela menor reduz o risco de POSTs gastarem
    # antes de detectar 403 e re-logarem. Re-login custa ~1 min
    # (subprocess Node em modo --login-only) e e' serializado entre
    # workers — so 1 loga, os outros 3 leem do arquivo.
    prazos_iniciais_legacy_task_session_ttl_minutes: int = 30

    # ── Disparo periódico do tratamento web (Onda 3 #6) ─────────────────
    # Worker que varre intakes com `dispatch_pending=True` e dispara
    # GED upload + enqueue cancel da legada em ordem cronológica.
    # Ligado por padrao em 2026-05-06 apos validacao operacional —
    # antes ficava desligado e o operador disparava 1 por 1 via UI
    # de debug. O batch_limit=10 da uma vazao razoavel sem inundar
    # o L1, e o intervalo 300s respeita o rate limit deles.
    prazos_iniciais_dispatch_enabled: bool = True
    prazos_iniciais_dispatch_interval_seconds: int = 300
    prazos_iniciais_dispatch_batch_limit: int = 10

    # ── Batch Tasks (OneSid, OneRequest, etc.) ────────────────────────
    # Chave(s) que autenticam as automações externas no endpoint
    # /api/v1/tasks/batch-create. Separado do JWT do operador pq o
    # OneSid chama direto via HTTP sem ter usuário/senha no sistema.
    # Aceita múltiplas separadas por vírgula pra rotação sem downtime.
    batch_tasks_api_key: str | None = None

    # ── Intake OneRequest (motor RPA externo) ─────────────────────────
    # Chave(s) que autenticam o motor RPA do OneRequest no endpoint
    # /api/v1/onerequest/intake/*. Aceita múltiplas separadas por vírgula
    # (rotação sem downtime). Vazio = endpoint de intake desativado.
    onerequest_intake_api_key: str | None = None

    # ── OneRequest: alerta "vence hoje" via Teams (Microsoft Graph) ────
    # Liga o botão "Enviar no Teams". A DM sai NO NOME da operadora logada,
    # via Graph delegado (MSAL no front + token repassado ao backend). Default
    # FALSE: enquanto não for true, o botão nem aparece (só "Copiar"). Setar no
    # Coolify quando o app do Entra estiver com plataforma SPA + permissões
    # Graph (Chat.Create, ChatMessage.Send, User.Read.All) consentidas.
    teams_alert_enabled: bool = False
    # Domínio M365 corporativo: só responsáveis com e-mail nesse domínio são
    # endereçáveis no Teams (os demais usam e-mail pessoal → só Copiar).
    teams_corporate_email_domain: str = "mdradvocacia.com"

    # ── OneRequest: sync read-only do Postgres da fonte (RPA escreve lá) ──
    # A RPA local escreve num Postgres separado (recurso Coolify do OneRequest);
    # o Flow LÊ de hora em hora e espelha pro onr_solicitacoes. NUNCA escreve na
    # fonte. Vazio = job de sync desligado. Ex.: postgresql://onerequest:SENHA@db:5432/onerequest
    # (o api do Flow precisa estar na rede do recurso OneRequest pra resolver "db").
    onerequest_source_db_url: str | None = None
    # Fase de transição: o tratamento (responsável/setor/data/anotação) é feito no
    # SISTEMA ANTIGO (a fonte). True = o sync ESPELHA esse tratamento no Flow
    # (sobrescreve). Na migração pro Flow, troque pra False no Coolify: o Flow vira
    # dono e o sync para de tocar no tratamento (só campos capturados + status).
    onerequest_sync_espelha_tratamento: bool = True

    # ── AJUS (sistema do cliente — POST /inserir-prazos) ──────────────
    # Credenciais lidas do env (Coolify). NÃO fica em tabela porque é
    # uma conta única por instalação MDR. Se um dia precisar de conta
    # por escritório, evolui pra tabela. Ver app/services/ajus/.
    ajus_base_url: str = "https://sistema.ajus.com.br/webservices/api"
    ajus_bearer_token: str | None = None
    ajus_cliente: str | None = None
    ajus_login: str | None = None
    ajus_senha: str | None = None
    # Storage local de cópias do PDF da habilitação que foram pra fila
    # AJUS. Sobrevive à rotina de cleanup do prazos_iniciais. Apagado
    # automaticamente após inserção bem-sucedida (sucesso da AJUS).
    ajus_storage_path: str = "/app/data/ajus_pdfs"

    # ── Classificação AJUS via RPA Playwright (Chunk 2) ───────────────
    # Selectors XPath, paths do portal e domínio do cliente NÃO ficam
    # aqui — viraram constantes em `app/services/ajus/portal_constants.py`
    # porque são fixos do portal AJUS / cliente MDR e não variam por
    # instância. Aqui sobra só o que é confidencial ou operacional.
    #
    # Volume persistente onde cada conta AJUS guarda seu storage_state.
    # Layout: <root>/<account_id>/storage_state.json. Volume é
    # compartilhado entre o container API e o `ajus-runner` (que roda
    # o Playwright). Em prod (Coolify) montar `/data/ajus-session/`.
    ajus_session_path: str = "/app/data/ajus-session"
    # Key Fernet pra criptografar a senha das contas AJUS na tabela
    # `ajus_session_accounts`. Gerar com Fernet.generate_key() — nunca
    # commitar valor real. Sem essa key configurada, o módulo de
    # classificação fica desabilitado (não loga, não dispara).
    ajus_fernet_key: str | None = None
    # Timeout do flow de login (ms) — usado em wait_for_login_outcome.
    # Ajustável via env se o portal estiver lento.
    ajus_login_outcome_timeout_ms: int = 30_000
    # Timeout do polling do IP-code (segundos). Operador tem esse tempo
    # pra submeter o código pela UI antes do runner desistir.
    ajus_ip_code_wait_seconds: int = 300
    # Worker do ajus-runner: intervalo entre polls e tamanho do batch
    # por conta em cada ciclo. 5 itens × 45s ≈ 4min/batch, então com
    # poll de 30s o worker fica idle a maior parte do tempo quando a
    # fila é pequena. Em backlog grande, sobe `ajus_runner_batch_per_account`.
    ajus_runner_poll_interval_seconds: int = 30
    ajus_runner_batch_per_account: int = 5

    # ── Distribuídos BB — coleta na nuvem via OneLog + Playwright ──────
    # OneLog é o broker de sessão do portal BB (HTTPS público, mesmo
    # usado por OneCost/Cadastro). Credenciais do robô (usuário tipo
    # `robo.cadastro`) — setar no Coolify, nunca commitar. Sem elas, a
    # coleta fica desabilitada (o resto do módulo funciona por ingestão).
    distribuidos_bb_onelog_api_url: str = "https://api-onelog.mdradvocacia.com"
    distribuidos_bb_onelog_username: str | None = None
    distribuidos_bb_onelog_password: str | None = None
    # Portal de notificações (consultar/receber) do PAJ.
    distribuidos_bb_portal_url: str = (
        "https://juridico.bb.com.br/paj/juridico/v2?app=consultarReceberNotificacoesApp"
    )
    # Chromium NÃO-headless (provado 2026-07-09: o PAJ bloqueia headless).
    # Roda sob Xvfb (DISPLAY setado no docker-api-start.sh). Só ligue
    # headless=True em ambiente onde o portal não faça anti-bot.
    distribuidos_bb_headless: bool = False
    # Trava de segurança GLOBAL da ciência: por run o operador pode ligar,
    # mas se isto estiver False a ciência (SIM) NUNCA é dada, aconteça o
    # que acontecer. Default seguro.
    distribuidos_bb_confirmar_ciencia: bool = False
    distribuidos_bb_session_timeout_seconds: int = 1800

    # Agendamento automático da coleta: 3x/dia (madrugada, meio-dia, noite),
    # horário de Brasília. Ao fim de cada passagem, gera e arquiva a planilha.
    # A ciência continua protegida pela trava global acima — mesmo agendado,
    # só dá SIM no BB se distribuidos_bb_confirmar_ciencia também estiver True.
    distribuidos_bb_agendamento_ativo: bool = True
    distribuidos_bb_agendamento_horarios: str = "3,12,20"  # horas BRT, separadas por vírgula

    # Monitor de cadastro no Legal One: de X em X min bate na API do L1 procurando
    # a pasta (por CNJ+escritório) dos processos com planilha já gerada, e confirma.
    distribuidos_bb_monitor_cadastro_ativo: bool = True
    distribuidos_bb_monitor_intervalo_min: int = 2

    # Reconsulta assíncrona do DataJud (Ativos): a planilha é a fonte primária do
    # cadastro; este worker enriquece a capa (classe/assunto/órgão) dos processos
    # cujo DataJud ainda estava pendente (recém-distribuído não indexado / timeout).
    distribuidos_bb_datajud_reconsult_ativo: bool = True
    distribuidos_bb_datajud_reconsult_intervalo_min: int = 30

    # Cadastro 100% automático: ao fim de uma coleta com processos distribuídos,
    # gera a planilha e importa no L1 (cria as pastas + dispara o workflow) sem
    # precisar do operador clicar. O monitor depois confirma cada pasta.
    distribuidos_bb_auto_cadastro_ativo: bool = True

    # ── GED LegalOne — envio em lote de arquivos pro GED (ECM) do L1 ──
    # Modulo dedicado a subir arquivos arbitrarios (PDF, docx, xlsx,
    # imagens...) no GED de processos do Legal One a partir de CNJ +
    # arquivo. Um worker em background processa cada lote item a item.
    # Volume persistente onde os arquivos ficam ate o upload no GED.
    ged_legalone_storage_path: str = "/app/data/ged_legalone"
    # Limite por arquivo (MB). PUT do Azure tem timeout de 60s — 50MB OK.
    ged_legalone_max_file_mb: int = 50
    # Extensoes aceitas (separadas por virgula). Rejeitadas cedo no create
    # (422) em vez de descobrir no meio do lote.
    ged_legalone_allowed_extensions: str = (
        "pdf,doc,docx,xls,xlsx,ppt,pptx,jpg,jpeg,png,txt,csv,zip"
    )
    # typeId default do GED (formato "type_N"). None = sem tipo (operador
    # define no L1). A UI manda o tipo por lote; isso e' so fallback.
    ged_legalone_default_type_id: str | None = None
    # Escape hatch: se o GED rejeitar a extensao original, refaz o upload
    # como octet-stream/pdf (bytes intactos). Default OFF — pode bagunçar
    # como o L1 renderiza o doc; ligar so se o GED tiver allow-list propria.
    ged_legalone_fallback_extension_to_pdf: bool = False
    # Worker de upload — CORE do modulo (nao dormente como o Classificador).
    # Default ON; em prod o Coolify pode desligar via env se precisar.
    ged_legalone_worker_enabled: bool = True
    ged_legalone_worker_interval_seconds: int = 15
    ged_legalone_worker_batch_size: int = 25
    # Itens travados em PROCESSANDO ha mais que isso (sem ged_document_id)
    # voltam pra PENDENTE no proximo tick (recuperacao de crash).
    ged_legalone_stuck_minutes: int = 15

    # ─── Atualizacao de Contatos LegalOne ────────────────────────────────
    # Enriquece contatos existentes (achados por CPF/CNPJ) com telefone/
    # e-mail/endereco via navigation property POST. Worker em background.
    contatos_legalone_worker_enabled: bool = True
    contatos_legalone_worker_interval_seconds: int = 10
    # Itens por tick. Cada item faz 1 busca + 3 leituras + ate' ~5 POSTs; o
    # _rate_limiter global do L1 (1.2 req/s) ja' serializa o throughput, entao
    # batch_size alto nao acelera — mantem baixo pra spread justo entre lotes.
    contatos_legalone_worker_batch_size: int = 5
    # Itens travados em PROCESSANDO ha mais que isso voltam pra PENDENTE.
    contatos_legalone_stuck_minutes: int = 15
    # typeId default de telefone (catalogo /ContactPhoneTypes): 3 = Celular.
    contatos_legalone_phone_type_id: int = 3
    # typeId default de e-mail (catalogo /ContactEmailTypes): 1 = Particular.
    contatos_legalone_email_type_id: int = 1
    # Formato do numero gravado: True = mascara "(92) 99202-2665" (default,
    # padrao do MD); False = so' digitos "92992022665".
    contatos_legalone_phone_keep_mask: bool = True


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def spreadsheet_max_size_bytes(self) -> int:
        return self.spreadsheet_max_size_mb * 1024 * 1024

    @property
    def allowed_spreadsheet_content_types(self) -> set[str]:
        return {
            content_type.strip().lower()
            for content_type in self.spreadsheet_allowed_content_types.split(",")
            if content_type.strip()
        }

    @property
    def prazos_iniciais_api_keys(self) -> set[str]:
        """Chaves válidas para autenticar a automação externa (aceita rotação)."""
        raw = self.prazos_iniciais_api_key or ""
        return {key.strip() for key in raw.split(",") if key.strip()}

    @property
    def classificador_api_keys(self) -> set[str]:
        """Chaves válidas pro endpoint público do Classificador (rotação)."""
        raw = self.classificador_api_key or ""
        return {key.strip() for key in raw.split(",") if key.strip()}

    @property
    def prazos_iniciais_max_pdf_bytes(self) -> int:
        return self.prazos_iniciais_max_pdf_mb * 1024 * 1024

    @property
    def prazos_iniciais_max_upload_pdf_bytes(self) -> int:
        return self.prazos_iniciais_max_upload_pdf_mb * 1024 * 1024

    @property
    def ged_legalone_max_file_bytes(self) -> int:
        return self.ged_legalone_max_file_mb * 1024 * 1024

    @property
    def ged_legalone_allowed_extensions_set(self) -> set[str]:
        """Extensoes aceitas pro envio ao GED, normalizadas (sem ponto, lower)."""
        raw = self.ged_legalone_allowed_extensions or ""
        return {
            ext.strip().lower().lstrip(".")
            for ext in raw.split(",")
            if ext.strip()
        }

    @property
    def batch_tasks_api_keys(self) -> set[str]:
        """
        Chaves válidas pra autenticar automações externas que chamam
        /api/v1/tasks/batch-create (OneSid, OneRequest etc.). Aceita
        múltiplas separadas por vírgula.
        """
        raw = self.batch_tasks_api_key or ""
        return {key.strip() for key in raw.split(",") if key.strip()}

    @property
    def onerequest_intake_api_keys(self) -> set[str]:
        """Chaves válidas pro intake do OneRequest (motor RPA externo, rotação)."""
        raw = self.onerequest_intake_api_key or ""
        return {key.strip() for key in raw.split(",") if key.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
