"""
Single source of truth for all 8 Lander verticals.

Structure per vertical:
  display_name   — UI label
  description    — one-sentence summary
  patterns       — regex fragments matched against job titles (case-insensitive, word-boundary)
  subcategories  — finer role groups within the vertical
  skills         — dict of canonical_name -> {aliases, category, weight}
  overlaps       — list of {pattern, primary, secondary} for ambiguous title segments

Skills dict keys are the canonical names used everywhere else (seed script, classifier, etc.).
"""

VERTICALS = {

    # ─────────────────────────────────────────────────────────────────────────
    "data_ml": {
        "display_name": "Data & AI",
        "description": "Roles focused on data pipelines, analytics, machine learning, and AI systems.",
        "patterns": [
            r"data engineer",
            r"data scientist",
            r"\bml engineer",
            r"machine learning",
            r"analytics engineer",
            r"\bai engineer",
            r"\bmlops\b",
            r"\bllmops\b",
            r"data analyst",
            r"applied scientist",
            r"research scientist",
            r"decision scientist",
            r"\bbi analyst",
            r"\bbi developer",
            r"business intelligence",
            r"quantitative analyst",
            r"quant analyst",
            r"data platform",
            r"data infrastructure",
            r"data architect",
            r"data manager",
            r"data lead",
            r"head of data",
            r"vp.*data",
            r"chief data",
            r"data product manager",       # primary data_ml, secondary product
        ],
        "subcategories": [
            "data_engineering",
            "data_science",
            "machine_learning",
            "analytics",
            "business_intelligence",
            "ai_research",
        ],
        "skills": {
            # ── Query / Warehouse ──
            "SQL": {
                "aliases": ["sql", "postgresql", "mysql", "bigquery sql", "snowflake sql", "redshift sql", "t-sql", "pl/sql", "spark sql"],
                "category": "query_language",
                "weight": 5,
                "also_in": ["engineering", "finance", "marketing", "product", "sales", "ops"],
            },
            "BigQuery": {
                "aliases": ["bigquery", "bq"],
                "category": "warehouse",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "Snowflake": {
                "aliases": ["snowflake", "snowflake data cloud"],
                "category": "warehouse",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "Redshift": {
                "aliases": ["redshift", "aws redshift", "amazon redshift"],
                "category": "warehouse",
                "weight": 3,
                "also_in": ["engineering"],
            },
            "Databricks": {
                "aliases": ["databricks", "databricks lakehouse"],
                "category": "warehouse",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "dbt": {
                "aliases": ["dbt", "data build tool", "dbt core"],
                "category": "transformation",
                "weight": 4,
            },
            # ── Language / Runtime ──
            "Python": {
                "aliases": ["python", "python3", "py", "cpython"],
                "category": "language",
                "weight": 5,
                "also_in": ["engineering", "finance"],
            },
            "R": {
                "aliases": ["r programming", "rlang", "tidyverse", "ggplot2", "dplyr"],
                "category": "language",
                "weight": 3,
                "also_in": ["finance"],
            },
            "Scala": {
                "aliases": ["scala"],
                "category": "language",
                "weight": 3,
                "also_in": ["engineering"],
            },
            # ── Data Processing ──
            "Spark": {
                "aliases": ["spark", "apache spark", "pyspark", "spark streaming"],
                "category": "data_processing",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "Kafka": {
                "aliases": ["kafka", "apache kafka", "confluent kafka", "confluent"],
                "category": "data_processing",
                "weight": 3,
                "also_in": ["engineering"],
            },
            "Flink": {
                "aliases": ["flink", "apache flink"],
                "category": "data_processing",
                "weight": 2,
                "also_in": ["engineering"],
            },
            "Hadoop": {
                "aliases": ["hadoop", "apache hadoop", "hdfs", "hive", "hbase"],
                "category": "data_processing",
                "weight": 2,
            },
            # ── Orchestration ──
            "Airflow": {
                "aliases": ["airflow", "apache airflow", "astronomer"],
                "category": "orchestration",
                "weight": 4,
            },
            "Prefect": {
                "aliases": ["prefect"],
                "category": "orchestration",
                "weight": 2,
            },
            "Dagster": {
                "aliases": ["dagster"],
                "category": "orchestration",
                "weight": 2,
            },
            "dbt Cloud": {
                "aliases": ["dbt cloud"],
                "category": "orchestration",
                "weight": 2,
            },
            # ── Python Data Stack ──
            "Pandas": {
                "aliases": ["pd"],
                "category": "data_library",
                "weight": 4,
            },
            "NumPy": {
                "aliases": ["np"],
                "category": "data_library",
                "weight": 3,
            },
            "Polars": {
                "aliases": ["polars"],
                "category": "data_library",
                "weight": 2,
            },
            "SciPy": {
                "aliases": ["scipy"],
                "category": "data_library",
                "weight": 2,
            },
            # ── ML / Modeling ──
            "scikit-learn": {
                "aliases": ["scikit-learn", "sklearn", "scikit learn"],
                "category": "ml_framework",
                "weight": 4,
            },
            "TensorFlow": {
                "aliases": ["tensorflow", "tf", "keras"],
                "category": "ml_framework",
                "weight": 4,
            },
            "PyTorch": {
                "aliases": ["pytorch", "torch"],
                "category": "ml_framework",
                "weight": 4,
            },
            "XGBoost": {
                "aliases": ["xgboost", "xgb", "lightgbm", "catboost", "gradient boosting"],
                "category": "ml_framework",
                "weight": 3,
            },
            "Hugging Face": {
                "aliases": ["hugging face", "huggingface", "diffusers"],
                "category": "ml_framework",
                "weight": 4,
            },
            "LangChain": {
                "aliases": ["langchain", "lang chain"],
                "category": "llm_tooling",
                "weight": 3,
            },
            "LlamaIndex": {
                "aliases": ["llamaindex", "llama index", "llama_index"],
                "category": "llm_tooling",
                "weight": 3,
            },
            "Large Language Models": {
                "aliases": ["large language models"],
                "category": "llm_tooling",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "Embeddings": {
                "aliases": ["embeddings", "vector embeddings", "sentence embeddings", "text embeddings"],
                "category": "llm_tooling",
                "weight": 3,
                "also_in": ["engineering"],
            },
            "RAG": {
                "aliases": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
                "category": "llm_tooling",
                "weight": 3,
                "also_in": ["engineering"],
            },
            "Vector Databases": {
                "aliases": ["pinecone", "weaviate", "qdrant", "chroma", "milvus", "pgvector", "vector database", "vector store"],
                "category": "llm_tooling",
                "weight": 3,
                "also_in": ["engineering"],
            },
            # ── MLOps ──
            "MLflow": {
                "aliases": ["mlflow", "ml flow"],
                "category": "mlops",
                "weight": 3,
            },
            "Kubeflow": {
                "aliases": ["kubeflow"],
                "category": "mlops",
                "weight": 2,
            },
            "Weights & Biases": {
                "aliases": ["weights & biases", "wandb", "weights and biases"],
                "category": "mlops",
                "weight": 3,
            },
            "SageMaker": {
                "aliases": ["aws sagemaker mlops"],
                "category": "mlops",
                "weight": 3,
                "also_in": ["engineering"],
            },
            "Vertex AI": {
                "aliases": ["google vertex ai", "vertexai"],
                "category": "mlops",
                "weight": 3,
                "also_in": ["engineering"],
            },
            # ── BI / Visualization ──
            "Tableau": {
                "aliases": ["tableau", "tableau desktop", "tableau server", "tableau cloud"],
                "category": "bi_tool",
                "weight": 4,
                "also_in": ["marketing", "product", "sales", "ops", "finance"],
            },
            "Looker": {
                "aliases": ["looker", "lookml", "looker studio", "google looker"],
                "category": "bi_tool",
                "weight": 4,
                "also_in": ["marketing", "product"],
            },
            "Power BI": {
                "aliases": ["power bi", "powerbi", "microsoft power bi"],
                "category": "bi_tool",
                "weight": 4,
                "also_in": ["finance", "ops"],
            },
            "Metabase": {
                "aliases": ["metabase"],
                "category": "bi_tool",
                "weight": 2,
            },
            "Mode": {
                "aliases": ["mode analytics", "mode"],
                "category": "bi_tool",
                "weight": 2,
            },
            # ── Cloud / Infra (data-relevant) ──
            "AWS": {
                "aliases": ["aws", "amazon web services", "s3", "ec2", "emr", "kinesis"],
                "category": "cloud",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "GCP": {
                "aliases": ["gcp", "google cloud", "google cloud platform", "gcs", "cloud storage", "dataflow", "pub/sub", "pubsub"],
                "category": "cloud",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "Azure": {
                "aliases": ["azure", "microsoft azure", "azure synapse", "azure data factory", "adls"],
                "category": "cloud",
                "weight": 3,
                "also_in": ["engineering"],
            },
            # ── A/B & Experimentation ──
            "A/B Testing": {
                "aliases": ["a/b testing", "ab testing", "experimentation", "split testing", "hypothesis testing"],
                "category": "statistics",
                "weight": 3,
                "also_in": ["marketing", "product"],
            },
            "Statistics": {
                "aliases": ["statistics", "bayesian statistics", "regression"],
                "category": "statistics",
                "weight": 3,
            },
        },
        "overlaps": [
            {
                "pattern": r"ml engineer|machine learning engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
                "note": "ML Engineers bridge data science and software engineering.",
            },
            {
                "pattern": r"\bai engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
                "note": "AI Engineers often build infrastructure as well as models.",
            },
            {
                "pattern": r"data product manager",
                "primary": "data_ml",
                "secondary": ["product"],
            },
            {
                "pattern": r"quant analyst|quantitative analyst",
                "primary": "data_ml",
                "secondary": ["finance"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "engineering": {
        "display_name": "Engineering",
        "description": "Software, infrastructure, hardware, and quality engineering roles.",
        "patterns": [
            r"software engineer",
            r"software developer",
            r"\bsde\b",
            r"\bswe\b",
            r"backend engineer",
            r"frontend engineer",
            r"front-end engineer",
            r"back-end engineer",
            r"fullstack engineer",
            r"full.stack engineer",
            r"full.stack developer",
            r"mobile engineer",
            r"\bios engineer",
            r"\bios developer",
            r"android engineer",
            r"android developer",
            r"\bdevops\b",
            r"site reliability",
            r"\bsre\b",
            r"platform engineer",
            r"infrastructure engineer",
            r"cloud engineer",
            r"security engineer",
            r"embedded engineer",
            r"embedded software",
            r"firmware engineer",
            r"hardware engineer",
            r"electrical engineer",
            r"mechanical engineer",
            r"robotics engineer",
            r"systems engineer",
            r"network engineer",
            r"distributed systems",
            r"\bqa engineer",
            r"test engineer",
            r"automation engineer",
            r"staff engineer",
            r"principal engineer",
            r"engineering manager",
            r"head of engineering",
            r"vp.*engineering",
            r"chief.*engineer",
        ],
        "subcategories": [
            "backend",
            "frontend",
            "fullstack",
            "mobile",
            "devops",
            "sre",
            "platform",
            "infra",
            "security",
            "embedded",
            "hardware",
            "qa",
        ],
        "skills": {
            "JavaScript": {
                "aliases": ["javascript", "js", "es6", "es2015", "ecmascript"],
                "category": "language",
                "weight": 5,
            },
            "TypeScript": {
                "aliases": ["typescript", "ts"],
                "category": "language",
                "weight": 5,
            },
            "Python": {
                "aliases": ["python", "python3"],
                "category": "language",
                "weight": 5,
                "also_in": ["data_ml"],
            },
            "Java": {
                "aliases": ["java", "java 8", "java 11", "java 17", "jvm"],
                "category": "language",
                "weight": 4,
            },
            "Go": {
                "aliases": ["go", "golang"],
                "category": "language",
                "weight": 4,
            },
            "Rust": {
                "aliases": ["rust", "rust-lang"],
                "category": "language",
                "weight": 3,
            },
            "C++": {
                "aliases": ["c++", "cpp", "c plus plus"],
                "category": "language",
                "weight": 3,
            },
            "C#": {
                "aliases": ["c#", "csharp", "c sharp", ".net", "dotnet", "asp.net"],
                "category": "language",
                "weight": 3,
            },
            "Ruby": {
                "aliases": ["ruby", "ruby on rails", "rails", "ror"],
                "category": "language",
                "weight": 3,
            },
            "PHP": {
                "aliases": ["php", "laravel", "symfony"],
                "category": "language",
                "weight": 2,
            },
            "Swift": {
                "aliases": ["swift", "swiftui", "objective-c", "objc"],
                "category": "language",
                "weight": 3,
            },
            "Kotlin": {
                "aliases": ["kotlin", "android kotlin"],
                "category": "language",
                "weight": 3,
            },
            # ── Frontend Frameworks ──
            "React": {
                "aliases": ["react", "reactjs", "react.js", "react native"],
                "category": "frontend_framework",
                "weight": 5,
            },
            "Vue": {
                "aliases": ["vue", "vuejs", "vue.js", "nuxt", "nuxt.js"],
                "category": "frontend_framework",
                "weight": 3,
            },
            "Angular": {
                "aliases": ["angular", "angularjs", "angular.js"],
                "category": "frontend_framework",
                "weight": 3,
            },
            "Next.js": {
                "aliases": ["next.js", "nextjs", "next js"],
                "category": "frontend_framework",
                "weight": 4,
            },
            "Svelte": {
                "aliases": ["svelte", "sveltekit"],
                "category": "frontend_framework",
                "weight": 2,
            },
            # ── Backend Frameworks ──
            "Node.js": {
                "aliases": ["node.js", "nodejs", "node js", "express", "express.js"],
                "category": "backend_framework",
                "weight": 4,
            },
            "Django": {
                "aliases": ["django", "django rest framework", "drf"],
                "category": "backend_framework",
                "weight": 3,
            },
            "Flask": {
                "aliases": ["flask"],
                "category": "backend_framework",
                "weight": 3,
            },
            "FastAPI": {
                "aliases": ["fastapi", "fast api"],
                "category": "backend_framework",
                "weight": 3,
            },
            "Spring": {
                "aliases": ["spring", "spring boot", "spring framework", "spring mvc"],
                "category": "backend_framework",
                "weight": 3,
            },
            "Rails": {
                "aliases": ["rails", "ruby on rails", "ror"],
                "category": "backend_framework",
                "weight": 2,
            },
            # ── Cloud / Infra ──
            "AWS": {
                "aliases": ["aws", "amazon web services"],
                "category": "cloud",
                "weight": 5,
                "also_in": ["data_ml"],
            },
            "GCP": {
                "aliases": ["gcp", "google cloud", "google cloud platform"],
                "category": "cloud",
                "weight": 4,
                "also_in": ["data_ml"],
            },
            "Azure": {
                "aliases": ["azure", "microsoft azure"],
                "category": "cloud",
                "weight": 4,
                "also_in": ["data_ml"],
            },
            "Docker": {
                "aliases": ["docker", "docker compose", "dockerfile", "containerization"],
                "category": "devops",
                "weight": 4,
            },
            "Kubernetes": {
                "aliases": ["kubernetes", "k8s", "helm", "eks", "gke", "aks"],
                "category": "devops",
                "weight": 4,
            },
            "Terraform": {
                "aliases": ["terraform", "hashicorp terraform", "tfstate", "opentofu"],
                "category": "devops",
                "weight": 4,
            },
            "Ansible": {
                "aliases": ["ansible", "ansible playbook"],
                "category": "devops",
                "weight": 2,
            },
            "CI/CD": {
                "aliases": ["ci/cd", "cicd", "github actions", "gitlab ci", "jenkins", "circleci", "travis ci", "buildkite", "argocd", "tekton"],
                "category": "devops",
                "weight": 4,
            },
            "Bash": {
                "aliases": ["ubuntu", "debian", "centos", "rhel"],
                "category": "devops",
                "weight": 3,
            },
            # ── Databases ──
            "PostgreSQL": {
                "aliases": ["pg"],
                "category": "database",
                "weight": 4,
            },
            "MySQL": {
                "aliases": ["mariadb"],
                "category": "database",
                "weight": 3,
            },
            "MongoDB": {
                "aliases": ["mongodb", "mongo", "mongoose"],
                "category": "database",
                "weight": 3,
            },
            "Redis": {
                "aliases": ["redis", "redis cache", "elasticache"],
                "category": "database",
                "weight": 3,
            },
            "Elasticsearch": {
                "aliases": ["elasticsearch", "elastic search", "opensearch", "elk stack"],
                "category": "database",
                "weight": 3,
            },
            # ── API / Architecture ──
            "GraphQL": {
                "aliases": ["graphql", "graph ql"],
                "category": "api",
                "weight": 3,
            },
            "REST": {
                "aliases": ["rest", "restful", "rest api", "restful api"],
                "category": "api",
                "weight": 4,
            },
            "Microservices": {
                "aliases": ["microservices", "micro services", "service mesh", "grpc", "protobuf"],
                "category": "architecture",
                "weight": 3,
            },
            "Git": {
                "aliases": ["git", "github", "gitlab", "bitbucket", "version control"],
                "category": "devops",
                "weight": 4,
            },
        },
        "overlaps": [
            {
                "pattern": r"security engineer|application security|appsec",
                "primary": "engineering",
                "secondary": ["ops"],
                "note": "Security engineers are counted as engineering; security specialists in ops.",
            },
            {
                "pattern": r"platform engineer",
                "primary": "engineering",
                "secondary": [],
                "note": "Platform engineers are squarely engineering.",
            },
            {
                "pattern": r"growth engineer",
                "primary": "engineering",
                "secondary": ["marketing"],
            },
            {
                "pattern": r"ml engineer|machine learning engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
            },
            {
                "pattern": r"\bai engineer",
                "primary": "data_ml",
                "secondary": ["engineering"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "finance": {
        "display_name": "Finance",
        "description": "Financial planning, accounting, investment, risk, and treasury roles.",
        "patterns": [
            r"\bfp&a\b",
            r"financial analyst",
            r"financial planning",
            r"financial reporting",
            r"\baccounti",          # accounting, accountant
            r"\baccountant\b",
            r"\bcontroller\b",
            r"treasury analyst",
            r"treasurer",
            r"\baudit",            # audit, auditor
            r"\bauditor\b",
            r"investment banker",
            r"investment banking",
            r"equity research",
            r"credit analyst",
            r"risk analyst",
            r"risk manager",
            r"\btax analyst",
            r"\btax manager",
            r"\bactuary\b",
            r"\bactuarial\b",
            r"underwriter",
            r"corporate finance",
            r"chief financial",
            r"\bcfo\b",
            r"vp.*finance",
            r"head of finance",
            r"finance manager",
            r"finance director",
            r"\bcpa\b",
            r"\bcfa\b",
        ],
        "subcategories": [
            "fpa",
            "accounting",
            "treasury",
            "audit",
            "investment",
            "risk",
            "tax",
        ],
        "skills": {
            "Excel": {
                "aliases": ["excel", "microsoft excel", "excel modeling", "vba", "pivot tables"],
                "category": "tool",
                "weight": 5,
                "also_in": ["ops", "marketing"],
            },
            "Financial Modeling": {
                "aliases": ["financial modeling", "financial modelling", "dcf", "lbo", "m&a modeling", "three-statement model"],
                "category": "finance_skill",
                "weight": 5,
            },
            "GAAP": {
                "aliases": ["gaap", "us gaap", "generally accepted accounting principles"],
                "category": "standard",
                "weight": 4,
            },
            "IFRS": {
                "aliases": ["ifrs", "international financial reporting standards"],
                "category": "standard",
                "weight": 3,
            },
            "SOX": {
                "aliases": ["sox", "sarbanes-oxley", "sarbanes oxley", "sox compliance"],
                "category": "standard",
                "weight": 3,
            },
            "NetSuite": {
                "aliases": ["netsuite", "oracle netsuite"],
                "category": "erp",
                "weight": 3,
                "also_in": ["ops"],
            },
            "SAP": {
                "aliases": ["sap", "sap erp", "sap s/4hana", "sap fi", "sap co"],
                "category": "erp",
                "weight": 3,
                "also_in": ["ops"],
            },
            "Oracle Financials": {
                "aliases": ["oracle financials", "oracle erp", "oracle cloud", "oracle fusion"],
                "category": "erp",
                "weight": 3,
                "also_in": ["ops"],
            },
            "QuickBooks": {
                "aliases": ["quickbooks", "quickbooks online", "qbo"],
                "category": "accounting_tool",
                "weight": 3,
            },
            "Hyperion": {
                "aliases": ["hyperion", "oracle hyperion", "hfm", "hyperion financial management", "epm"],
                "category": "fpa_tool",
                "weight": 3,
            },
            "Anaplan": {
                "aliases": ["anaplan"],
                "category": "fpa_tool",
                "weight": 3,
            },
            "TM1": {
                "aliases": ["tm1", "ibm planning analytics", "ibm tm1"],
                "category": "fpa_tool",
                "weight": 2,
            },
            "Workday Financials": {
                "aliases": ["workday financials", "workday finance", "workday adaptive"],
                "category": "fpa_tool",
                "weight": 3,
                "also_in": ["ops"],
            },
            "Bloomberg Terminal": {
                "aliases": ["bloomberg", "bloomberg terminal", "bloomberg professional"],
                "category": "investment_tool",
                "weight": 4,
            },
            "FactSet": {
                "aliases": ["factset"],
                "category": "investment_tool",
                "weight": 3,
            },
            "Capital IQ": {
                "aliases": ["capital iq", "capiq", "s&p capital iq"],
                "category": "investment_tool",
                "weight": 3,
            },
            "Valuation": {
                "aliases": ["valuation", "equity valuation", "company valuation", "business valuation"],
                "category": "finance_skill",
                "weight": 4,
            },
            "SQL": {
                "aliases": ["sql"],
                "category": "query_language",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "Python": {
                "aliases": ["python", "python3"],
                "category": "language",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "R": {
                "aliases": ["r programming", "rlang"],
                "category": "language",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "Tableau": {
                "aliases": ["tableau"],
                "category": "bi_tool",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "Power BI": {
                "aliases": ["power bi", "powerbi"],
                "category": "bi_tool",
                "weight": 3,
                "also_in": ["data_ml"],
            },
        },
        "overlaps": [
            {
                "pattern": r"quant analyst|quantitative analyst|quantitative researcher",
                "primary": "data_ml",
                "secondary": ["finance"],
                "note": "Quant roles are primarily data_ml but live in finance contexts.",
            },
            {
                "pattern": r"financial data analyst|finance data",
                "primary": "data_ml",
                "secondary": ["finance"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "marketing": {
        "display_name": "Marketing",
        "description": "Growth, performance, content, brand, and marketing operations roles.",
        "patterns": [
            r"marketing manager",
            r"marketing director",
            r"marketing specialist",
            r"marketing analyst",
            r"marketing coordinator",
            r"growth manager",
            r"growth marketing",
            r"demand gen",
            r"demand generation",
            r"lifecycle marketing",
            r"lifecycle manager",
            r"performance marketing",
            r"paid acquisition",
            r"paid media",
            r"paid social",
            r"\bseo\b",
            r"\bsem\b",
            r"search engine",
            r"content marketing",
            r"content strategist",
            r"content manager",
            r"content writer",
            r"copywriter",
            r"\bbrand manager",
            r"\bbrand strategist",
            r"communications manager",
            r"public relations",
            r"\bpr manager",
            r"social media manager",
            r"social media specialist",
            r"email marketing",
            r"product marketing",
            r"\bpmm\b",
            r"marketing ops",
            r"\bmarops\b",
            r"creative director",
            r"vp.*marketing",
            r"chief marketing",
            r"\bcmo\b",
            r"head of marketing",
            r"head of growth",
        ],
        "subcategories": [
            "growth",
            "performance",
            "content",
            "brand",
            "communications",
            "lifecycle",
            "marketing_ops",
            "product_marketing",
        ],
        "skills": {
            "Google Analytics": {
                "aliases": ["google analytics", "universal analytics", "ua"],
                "category": "analytics",
                "weight": 5,
            },
            "Google Ads": {
                "aliases": ["google ads", "google adwords", "adwords", "ppc", "sem"],
                "category": "paid_media",
                "weight": 4,
            },
            "Meta Ads": {
                "aliases": ["meta ads", "facebook ads", "instagram ads", "facebook advertising", "meta advertising"],
                "category": "paid_media",
                "weight": 4,
            },
            "LinkedIn Ads": {
                "aliases": ["linkedin ads", "linkedin advertising", "linkedin campaign manager"],
                "category": "paid_media",
                "weight": 3,
            },
            "HubSpot": {
                "aliases": ["hubspot", "hubspot crm", "hubspot marketing hub"],
                "category": "marketing_automation",
                "weight": 4,
                "also_in": ["sales"],
            },
            "Marketo": {
                "aliases": ["marketo", "adobe marketo", "marketo engage"],
                "category": "marketing_automation",
                "weight": 3,
            },
            "Salesforce": {
                "aliases": ["salesforce", "sfdc", "salesforce crm"],
                "category": "crm",
                "weight": 4,
                "also_in": ["sales", "ops"],
            },
            "Pardot": {
                "aliases": ["pardot", "salesforce pardot", "marketing cloud account engagement"],
                "category": "marketing_automation",
                "weight": 3,
            },
            "Iterable": {
                "aliases": ["iterable"],
                "category": "lifecycle_tool",
                "weight": 3,
            },
            "Customer.io": {
                "aliases": ["customer.io", "customerio"],
                "category": "lifecycle_tool",
                "weight": 3,
            },
            "Klaviyo": {
                "aliases": ["klaviyo"],
                "category": "lifecycle_tool",
                "weight": 3,
            },
            "Braze": {
                "aliases": ["braze"],
                "category": "lifecycle_tool",
                "weight": 3,
            },
            "Mailchimp": {
                "aliases": ["mailchimp", "mail chimp"],
                "category": "lifecycle_tool",
                "weight": 2,
            },
            "Attentive": {
                "aliases": ["attentive", "attentive mobile"],
                "category": "lifecycle_tool",
                "weight": 2,
            },
            "SEMrush": {
                "aliases": ["semrush", "sem rush"],
                "category": "seo_tool",
                "weight": 3,
            },
            "Ahrefs": {
                "aliases": ["ahrefs"],
                "category": "seo_tool",
                "weight": 3,
            },
            "Moz": {
                "aliases": ["moz", "moz pro"],
                "category": "seo_tool",
                "weight": 2,
            },
            "Hotjar": {
                "aliases": ["hotjar"],
                "category": "analytics",
                "weight": 2,
                "also_in": ["product"],
            },
            "Mixpanel": {
                "aliases": ["mixpanel"],
                "category": "analytics",
                "weight": 3,
                "also_in": ["product", "data_ml"],
            },
            "Amplitude": {
                "aliases": ["amplitude"],
                "category": "analytics",
                "weight": 3,
                "also_in": ["product", "data_ml"],
            },
            "Segment": {
                "aliases": ["segment", "twilio segment"],
                "category": "cdp",
                "weight": 3,
                "also_in": ["data_ml", "product"],
            },
            "Looker": {
                "aliases": ["looker", "lookml"],
                "category": "bi_tool",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "SQL": {
                "aliases": ["sql"],
                "category": "query_language",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "Adobe Creative Cloud": {
                "aliases": ["adobe creative cloud", "adobe cc", "adobe suite"],
                "category": "design_tool",
                "weight": 3,
                "also_in": ["design"],
            },
            "Figma": {
                "aliases": ["figma"],
                "category": "design_tool",
                "weight": 3,
                "also_in": ["design", "product"],
            },
            "A/B Testing": {
                "aliases": ["a/b testing", "ab testing", "conversion rate optimization", "cro"],
                "category": "experimentation",
                "weight": 4,
                "also_in": ["data_ml", "product"],
            },
            "Copywriting": {
                "aliases": ["copywriting", "copy writing", "content writing", "ux writing"],
                "category": "soft_skill",
                "weight": 3,
            },
        },
        "overlaps": [
            {
                "pattern": r"marketing analyst|marketing data",
                "primary": "data_ml",
                "secondary": ["marketing"],
                "note": "Analytics-heavy marketing roles lean data_ml.",
            },
            {
                "pattern": r"growth engineer",
                "primary": "engineering",
                "secondary": ["marketing"],
            },
            {
                "pattern": r"product marketing",
                "primary": "marketing",
                "secondary": ["product"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "product": {
        "display_name": "Product",
        "description": "Product management, product operations, and product analytics roles.",
        "patterns": [
            r"product manager",
            r"\bpm\b",
            r"product owner",
            r"technical product manager",
            r"\btpm\b",
            r"product ops",
            r"product analyst",
            r"group product manager",
            r"\bgpm\b",
            r"principal pm",
            r"head of product",
            r"vp.*product",
            r"chief product",
            r"\bcpo\b",
            r"director of product",
            r"sr\. product",
            r"senior product manager",
            r"associate product manager",
            r"\bapm\b",
        ],
        "subcategories": [
            "product_management",
            "product_ops",
            "product_analytics",
            "technical_pm",
        ],
        "skills": {
            "Jira": {
                "aliases": ["jira", "atlassian jira", "jira software"],
                "category": "pm_tool",
                "weight": 4,
                "also_in": ["engineering", "ops"],
            },
            "Confluence": {
                "aliases": ["confluence", "atlassian confluence"],
                "category": "pm_tool",
                "weight": 3,
                "also_in": ["engineering"],
            },
            "Notion": {
                "aliases": ["notion"],
                "category": "pm_tool",
                "weight": 3,
                "also_in": ["ops"],
            },
            "Productboard": {
                "aliases": ["productboard"],
                "category": "pm_tool",
                "weight": 3,
            },
            "Aha!": {
                "aliases": ["aha!", "aha roadmap"],
                "category": "pm_tool",
                "weight": 2,
            },
            "Linear": {
                "aliases": ["linear", "linear app"],
                "category": "pm_tool",
                "weight": 3,
                "also_in": ["engineering"],
            },
            "Asana": {
                "aliases": ["asana"],
                "category": "pm_tool",
                "weight": 3,
                "also_in": ["ops"],
            },
            "Mixpanel": {
                "aliases": ["mixpanel"],
                "category": "analytics",
                "weight": 4,
                "also_in": ["marketing", "data_ml"],
            },
            "Amplitude": {
                "aliases": ["amplitude"],
                "category": "analytics",
                "weight": 4,
                "also_in": ["marketing", "data_ml"],
            },
            "Pendo": {
                "aliases": ["pendo"],
                "category": "analytics",
                "weight": 3,
            },
            "Heap": {
                "aliases": ["heap", "heap analytics"],
                "category": "analytics",
                "weight": 2,
            },
            "Hotjar": {
                "aliases": ["hotjar"],
                "category": "ux_research_tool",
                "weight": 3,
                "also_in": ["marketing"],
            },
            "Figma": {
                "aliases": ["figma"],
                "category": "design_tool",
                "weight": 4,
                "also_in": ["design", "marketing"],
            },
            "SQL": {
                "aliases": ["sql"],
                "category": "query_language",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "Tableau": {
                "aliases": ["tableau"],
                "category": "bi_tool",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "OKRs": {
                "aliases": ["okrs", "okr", "objectives and key results"],
                "category": "pm_methodology",
                "weight": 3,
            },
            "Roadmapping": {
                "aliases": ["roadmapping", "product roadmap", "roadmap planning"],
                "category": "pm_methodology",
                "weight": 3,
            },
            "Agile": {
                "aliases": ["agile", "scrum", "kanban", "sprint planning", "sprint review"],
                "category": "pm_methodology",
                "weight": 4,
                "also_in": ["engineering"],
            },
            "User Research": {
                "aliases": ["user research", "ux research", "customer research", "usability testing", "user interviews"],
                "category": "pm_methodology",
                "weight": 4,
                "also_in": ["design"],
            },
            "Optimizely": {
                "aliases": ["optimizely"],
                "category": "experimentation",
                "weight": 3,
                "also_in": ["marketing", "data_ml"],
            },
            "A/B Testing": {
                "aliases": ["a/b testing", "ab testing", "experimentation"],
                "category": "pm_methodology",
                "weight": 3,
                "also_in": ["data_ml", "marketing"],
            },
        },
        "overlaps": [
            {
                "pattern": r"product analyst",
                "primary": "product",
                "secondary": ["data_ml"],
            },
            {
                "pattern": r"technical product manager|technical pm",
                "primary": "product",
                "secondary": ["engineering"],
            },
            {
                "pattern": r"data product manager",
                "primary": "data_ml",
                "secondary": ["product"],
            },
            {
                "pattern": r"product marketing",
                "primary": "marketing",
                "secondary": ["product"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "sales": {
        "display_name": "Sales",
        "description": "Account executives, SDRs, customer success, revenue ops, and sales engineering roles.",
        "patterns": [
            r"account executive",
            r"\bae\b",
            r"\bsdr\b",
            r"\bbdr\b",
            r"business development rep",
            r"business development manager",
            r"sales engineer",
            r"solutions engineer",
            r"solutions architect",  # often sales-adjacent
            r"customer success",
            r"\bcsm\b",
            r"account manager",
            r"\bam\b",
            r"revenue ops",
            r"\brevops\b",
            r"sales ops",
            r"sales operations",
            r"channel sales",
            r"field sales",
            r"inside sales",
            r"enterprise sales",
            r"sales manager",
            r"sales director",
            r"vp.*sales",
            r"chief revenue",
            r"\bcro\b",
            r"head of sales",
            r"partnership manager",
            r"alliances manager",
            r"sales development",
        ],
        "subcategories": [
            "account_executive",
            "bdr_sdr",
            "sales_engineering",
            "customer_success",
            "sales_ops",
            "account_management",
        ],
        "skills": {
            "Salesforce": {
                "aliases": ["salesforce", "sfdc", "salesforce crm", "salesforce sales cloud"],
                "category": "crm",
                "weight": 5,
                "also_in": ["marketing", "ops"],
            },
            "HubSpot": {
                "aliases": ["hubspot", "hubspot crm", "hubspot sales hub"],
                "category": "crm",
                "weight": 4,
                "also_in": ["marketing"],
            },
            "Outreach": {
                "aliases": ["outreach", "outreach.io"],
                "category": "sales_engagement",
                "weight": 4,
            },
            "SalesLoft": {
                "aliases": ["salesloft", "sales loft"],
                "category": "sales_engagement",
                "weight": 3,
            },
            "Gong": {
                "aliases": ["gong", "gong.io"],
                "category": "call_intelligence",
                "weight": 3,
            },
            "Chorus": {
                "aliases": ["chorus", "chorus.ai", "zoominfo chorus"],
                "category": "call_intelligence",
                "weight": 2,
            },
            "ZoomInfo": {
                "aliases": ["zoominfo", "zoom info"],
                "category": "prospecting",
                "weight": 3,
            },
            "LinkedIn Sales Navigator": {
                "aliases": ["linkedin sales navigator", "sales navigator", "linkedin navigator"],
                "category": "prospecting",
                "weight": 3,
            },
            "Apollo": {
                "aliases": ["apollo", "apollo.io"],
                "category": "prospecting",
                "weight": 3,
            },
            "Tableau": {
                "aliases": ["tableau"],
                "category": "bi_tool",
                "weight": 2,
                "also_in": ["data_ml"],
            },
            "SQL": {
                "aliases": ["sql"],
                "category": "query_language",
                "weight": 2,
                "also_in": ["data_ml"],
            },
            "MEDDIC": {
                "aliases": ["meddic", "meddpicc", "medd-ic"],
                "category": "sales_methodology",
                "weight": 3,
            },
            "Sandler": {
                "aliases": ["sandler", "sandler selling"],
                "category": "sales_methodology",
                "weight": 2,
            },
            "Challenger": {
                "aliases": ["challenger sale", "challenger selling"],
                "category": "sales_methodology",
                "weight": 2,
            },
            "SPIN Selling": {
                "aliases": ["spin selling", "spin", "spin methodology"],
                "category": "sales_methodology",
                "weight": 2,
            },
            "Pipeline Management": {
                "aliases": ["pipeline management", "pipeline forecasting", "quota management", "quota attainment"],
                "category": "sales_skill",
                "weight": 4,
            },
        },
        "overlaps": [
            {
                "pattern": r"sales engineer|solutions engineer",
                "primary": "sales",
                "secondary": ["engineering"],
                "note": "Sales engineers sit in sales org but need deep technical knowledge.",
            },
            {
                "pattern": r"revenue ops|revops|sales ops",
                "primary": "sales",
                "secondary": ["ops"],
            },
            {
                "pattern": r"solutions architect",
                "primary": "sales",
                "secondary": ["engineering"],
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "design": {
        "display_name": "Design",
        "description": "Product design, UX research, brand, motion, and design operations roles.",
        "patterns": [
            r"product designer",
            r"ux designer",
            r"ui designer",
            r"ux/ui designer",
            r"user experience designer",
            r"user interface designer",
            r"visual designer",
            r"brand designer",
            r"graphic designer",
            r"motion designer",
            r"creative director",
            r"design director",
            r"design ops",
            r"interaction designer",
            r"industrial designer",
            r"ux researcher",
            r"design researcher",
            r"user researcher",
            r"head of design",
            r"vp.*design",
            r"chief design",
            r"\bcdo\b",             # chief design officer
            r"design lead",
            r"lead designer",
            r"senior designer",
            r"staff designer",
        ],
        "subcategories": [
            "product_design",
            "ux_research",
            "brand_design",
            "motion",
            "design_ops",
            "interaction",
        ],
        "skills": {
            "Figma": {
                "aliases": ["figma", "figma components", "figma prototyping"],
                "category": "design_tool",
                "weight": 5,
                "also_in": ["product", "marketing"],
            },
            "Sketch": {
                "aliases": ["sketch", "sketch app"],
                "category": "design_tool",
                "weight": 3,
            },
            "Adobe XD": {
                "aliases": ["adobe xd", "xd"],
                "category": "design_tool",
                "weight": 2,
            },
            "Adobe Photoshop": {
                "aliases": ["photoshop", "adobe photoshop", "ps"],
                "category": "design_tool",
                "weight": 3,
                "also_in": ["marketing"],
            },
            "Adobe Illustrator": {
                "aliases": ["illustrator", "adobe illustrator"],
                "category": "design_tool",
                "weight": 3,
                "also_in": ["marketing"],
            },
            "Adobe InDesign": {
                "aliases": ["indesign", "adobe indesign"],
                "category": "design_tool",
                "weight": 2,
                "also_in": ["marketing"],
            },
            "After Effects": {
                "aliases": ["after effects", "adobe after effects", "ae"],
                "category": "motion_tool",
                "weight": 3,
            },
            "Premiere Pro": {
                "aliases": ["premiere", "adobe premiere", "premiere pro"],
                "category": "motion_tool",
                "weight": 2,
            },
            "Cinema 4D": {
                "aliases": ["cinema 4d", "c4d"],
                "category": "motion_tool",
                "weight": 2,
            },
            "Blender": {
                "aliases": ["blender", "blender 3d"],
                "category": "motion_tool",
                "weight": 2,
            },
            "Principle": {
                "aliases": ["principle", "principle app"],
                "category": "prototyping_tool",
                "weight": 2,
            },
            "Framer": {
                "aliases": ["framer", "framer motion"],
                "category": "prototyping_tool",
                "weight": 2,
            },
            "Webflow": {
                "aliases": ["webflow"],
                "category": "prototyping_tool",
                "weight": 2,
            },
            "Zeplin": {
                "aliases": ["zeplin"],
                "category": "handoff_tool",
                "weight": 2,
            },
            "Abstract": {
                "aliases": ["abstract", "abstract version control"],
                "category": "design_ops_tool",
                "weight": 1,
            },
            "Maze": {
                "aliases": ["maze", "maze.co"],
                "category": "ux_research_tool",
                "weight": 2,
            },
            "UserTesting": {
                "aliases": ["usertesting", "user testing"],
                "category": "ux_research_tool",
                "weight": 3,
                "also_in": ["product"],
            },
            "Lookback": {
                "aliases": ["lookback", "lookback.io"],
                "category": "ux_research_tool",
                "weight": 2,
            },
            "Lottie": {
                "aliases": ["lottie", "lottiefiles"],
                "category": "motion_tool",
                "weight": 2,
            },
            "Design Systems": {
                "aliases": ["design system", "design systems", "component library", "style guide", "token system"],
                "category": "design_skill",
                "weight": 4,
            },
            "Prototyping": {
                "aliases": ["prototyping", "rapid prototyping", "high-fidelity prototype", "low-fidelity prototype"],
                "category": "design_skill",
                "weight": 4,
            },
            "Wireframing": {
                "aliases": ["wireframing", "wireframes", "wireframe"],
                "category": "design_skill",
                "weight": 3,
            },
            "User Research": {
                "aliases": ["user research", "ux research", "usability testing", "user interviews", "contextual inquiry"],
                "category": "design_skill",
                "weight": 4,
                "also_in": ["product"],
            },
        },
        "overlaps": [
            {
                "pattern": r"ux researcher|design researcher|user researcher",
                "primary": "design",
                "secondary": ["product"],
                "note": "UX researchers sit in design orgs but closely tied to product.",
            },
            {
                "pattern": r"motion designer",
                "primary": "design",
                "secondary": [],
            },
            {
                "pattern": r"creative director",
                "primary": "design",
                "secondary": ["marketing"],
                "note": "Creative directors in brand/agency contexts lean marketing.",
            },
        ],
    },

    # ─────────────────────────────────────────────────────────────────────────
    "ops": {
        "display_name": "Operations",
        "description": "Business operations, supply chain, people ops, talent acquisition, and executive support roles.",
        "patterns": [
            r"business operations",
            r"\bbizops\b",
            r"strategic ops",
            r"strategy & operations",
            r"supply chain",
            r"logistics manager",
            r"logistics coordinator",
            r"procurement manager",
            r"vendor management",
            r"people ops",
            r"people operations",
            r"talent ops",
            r"hr ops",
            r"hr operations",
            r"\brecruiter\b",
            r"talent acquisition",
            r"\bta \b",
            r"recruiting manager",
            r"head of recruiting",
            r"executive assistant",
            r"office manager",
            r"facilities manager",
            r"chief of staff",
            r"vp.*operations",
            r"head of operations",
            r"coo",
            r"director of operations",
            r"program manager",     # can overlap with product; ops is primary without "product" in title
        ],
        "subcategories": [
            "business_ops",
            "supply_chain",
            "people_ops",
            "talent_acquisition",
            "executive_support",
            "facilities",
        ],
        "skills": {
            "Excel": {
                "aliases": ["excel", "microsoft excel"],
                "category": "tool",
                "weight": 4,
                "also_in": ["finance"],
            },
            "SQL": {
                "aliases": ["sql"],
                "category": "query_language",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "Tableau": {
                "aliases": ["tableau"],
                "category": "bi_tool",
                "weight": 3,
                "also_in": ["data_ml"],
            },
            "Salesforce": {
                "aliases": ["salesforce", "sfdc"],
                "category": "crm",
                "weight": 3,
                "also_in": ["sales", "marketing"],
            },
            "Workday HCM": {
                "aliases": ["workday", "workday hcm", "workday hr"],
                "category": "hris",
                "weight": 4,
            },
            "Greenhouse": {
                "aliases": ["greenhouse", "greenhouse recruiting"],
                "category": "ats",
                "weight": 3,
            },
            "BambooHR": {
                "aliases": ["bamboohr", "bamboo hr"],
                "category": "hris",
                "weight": 3,
            },
            "Lever": {
                "aliases": ["lever", "lever ats"],
                "category": "ats",
                "weight": 3,
            },
            "Asana": {
                "aliases": ["asana"],
                "category": "pm_tool",
                "weight": 3,
                "also_in": ["product"],
            },
            "Monday.com": {
                "aliases": ["monday.com", "monday", "monday work os"],
                "category": "pm_tool",
                "weight": 3,
            },
            "Notion": {
                "aliases": ["notion"],
                "category": "pm_tool",
                "weight": 3,
                "also_in": ["product"],
            },
            "Smartsheet": {
                "aliases": ["smartsheet"],
                "category": "pm_tool",
                "weight": 2,
            },
            "NetSuite": {
                "aliases": ["netsuite", "oracle netsuite"],
                "category": "erp",
                "weight": 3,
                "also_in": ["finance"],
            },
            "SAP": {
                "aliases": ["sap", "sap erp"],
                "category": "erp",
                "weight": 3,
                "also_in": ["finance"],
            },
            "Coupa": {
                "aliases": ["coupa", "coupa software"],
                "category": "procurement_tool",
                "weight": 2,
            },
            "Procurify": {
                "aliases": ["procurify"],
                "category": "procurement_tool",
                "weight": 1,
            },
            "ERP Systems": {
                "aliases": ["erp systems", "erp"],
                "category": "erp",
                "weight": 2,
                "also_in": ["finance"],
            },
            "Power Automate": {
                "aliases": ["power automate", "microsoft power automate", "ms power automate"],
                "category": "automation_tool",
                "weight": 2,
                "also_in": ["engineering"],
            },
            "Project Management": {
                "aliases": ["project management", "program management", "pmp", "prince2"],
                "category": "methodology",
                "weight": 4,
            },
            "Lean": {
                "aliases": ["lean", "lean methodology", "lean six sigma"],
                "category": "methodology",
                "weight": 2,
            },
            "Six Sigma": {
                "aliases": ["six sigma", "6 sigma", "dmaic"],
                "category": "methodology",
                "weight": 2,
            },
            "Change Management": {
                "aliases": ["change management", "organizational change"],
                "category": "methodology",
                "weight": 3,
            },
        },
        "overlaps": [
            {
                "pattern": r"recruiting ops|talent ops",
                "primary": "ops",
                "secondary": [],
                "note": "Recruiting ops is firmly ops; pure recruiter roles also ops.",
            },
            {
                "pattern": r"strategic ops|strategy.*operations",
                "primary": "ops",
                "secondary": ["product"],
                "note": "Strategic ops at startups often adjacent to product/strategy.",
            },
            {
                "pattern": r"revenue ops|revops",
                "primary": "sales",
                "secondary": ["ops"],
            },
            {
                "pattern": r"program manager",
                "primary": "ops",
                "secondary": ["product", "engineering"],
                "note": "Program manager is ops by default unless 'product' or 'technical' appears in title.",
            },
        ],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Convenience helpers used by classify_domain.py and seed_skills_taxonomy.py
# ─────────────────────────────────────────────────────────────────────────────

VERTICAL_KEYS: list[str] = list(VERTICALS.keys())
VERTICAL_DISPLAY: dict[str, str] = {k: v["display_name"] for k, v in VERTICALS.items()}


def all_patterns_for(vertical: str) -> list[str]:
    """Return the raw pattern strings for a given vertical."""
    return VERTICALS[vertical]["patterns"]


def all_skills_for(vertical: str) -> dict:
    """Return the skills dict for a given vertical."""
    return VERTICALS[vertical]["skills"]


def overlap_rules() -> list[dict]:
    """Return all overlap rules across every vertical (deduplicated by pattern)."""
    seen: set[str] = set()
    rules: list[dict] = []
    for vertical, data in VERTICALS.items():
        for rule in data.get("overlaps", []):
            if rule["pattern"] not in seen:
                seen.add(rule["pattern"])
                rules.append({**rule, "_defined_in": vertical})
    return rules


if __name__ == "__main__":
    # Quick sanity check
    total_skills = sum(len(v["skills"]) for v in VERTICALS.values())
    print(f"Verticals: {len(VERTICALS)}")
    print(f"Total skill definitions: {total_skills}")
    for key, v in VERTICALS.items():
        print(f"  {key:15s} — {len(v['patterns']):2d} patterns, {len(v['skills']):2d} skills, {len(v['subcategories'])} subcategories")
