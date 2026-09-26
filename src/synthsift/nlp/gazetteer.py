"""Curated vocabularies that complement spaCy NER and WordNet.

Two lists per category:

* ``TERMS`` – matched case-insensitively
* ``CASED`` – ambiguous words that only count when capitalised as written
  ("Rust" the language vs "rust" on a wheel arch)

Users add their own lists in Settings → Custom vocabulary.
"""

from __future__ import annotations

TERMS: dict[str, list[str]] = {
    "software": [
        # languages
        "javascript", "typescript", "golang", "kotlin", "scala", "haskell", "clojure", "elixir", "erlang",
        "c++", "c#", "objective-c", "php", "perl", "bash", "zsh", "powershell", "sql", "graphql", "html",
        "css", "webassembly", "wasm", "fortran", "cobol", "matlab", "julia", "lua", "dart", "zig", "ocaml",
        # runtimes, package managers, build tools
        "node.js", "nodejs", "deno", "npm", "pnpm", "pip", "pipx", "uv", "poetry", "conda",
        "maven", "gradle", "webpack", "vite", "esbuild", "babel", "cmake", "bazel",
        # infra & ops
        "docker", "podman", "kubernetes", "k8s", "kubectl", "helm", "terraform", "ansible", "pulumi",
        "nginx", "haproxy", "istio", "prometheus", "grafana", "loki", "jaeger",
        "elasticsearch", "kibana", "logstash", "kafka", "rabbitmq", "zookeeper",
        "jenkins", "github actions", "gitlab", "circleci", "argocd", "systemd", "cron", "crontab",
        "git", "github", "bitbucket", "vim", "neovim", "emacs", "vscode", "vs code", "tmux", "ssh",
        # data
        "postgres", "postgresql", "mysql", "mariadb", "sqlite", "redis", "memcached", "mongodb",
        "cassandra", "dynamodb", "bigquery", "snowflake", "clickhouse", "duckdb", "pandas", "numpy",
        "polars", "hadoop", "airflow", "dbt", "jupyter",
        # frameworks & libraries
        "vue", "angular", "svelte", "next.js", "nextjs", "django", "flask", "fastapi",
        "rails", "laravel", "spring boot", "pytorch", "tensorflow", "keras", "scikit-learn",
        "sklearn", "spacy", "nltk", "networkx", "pyvis", "matplotlib", "plotly", "pydantic", "sqlalchemy",
        "pytest", "playwright", "selenium", "jquery",
        # os / platforms
        "linux", "ubuntu", "debian", "fedora", "centos", "alpine", "arch linux", "macos",
        "android", "ios", "freebsd", "wsl",
        # cloud
        "aws", "gcp", "azure", "s3", "ec2", "lambda", "cloudflare", "vercel", "netlify", "heroku",
        "firebase", "supabase", "cloud run", "kubernetes engine", "eks", "gke", "aks",
        # protocols & formats
        "http", "https", "tcp", "udp", "dns", "tls", "ssl", "smtp", "imap", "ftp", "sftp", "grpc",
        "websocket", "rest api", "json", "yaml", "toml", "xml", "csv", "markdown", "protobuf", "oauth",
        "jwt", "saml", "ldap", "vpn", "wireguard", "openvpn", "ipsec",
        # generic tech nouns that WordNet files elsewhere
        "server", "router", "repository", "repo", "endpoint", "api", "cache",
        "localhost", "firewall", "load balancer", "container image", "dockerfile", "virtualenv",
        "venv", "cli", "sdk", "ci", "stack trace", "traceback", "segfault", "bug", "debugger",
    ],
    "cloud": ["iam role", "security group", "vpc", "subnet", "s3 bucket", "service account"],
    "product": [
        # AI models & assistants
        "claude", "chatgpt", "gpt-4", "gpt-4o", "gpt-5", "gemini", "mixtral",
        "copilot", "codex", "deepseek", "qwen",
    ],
    "vehicle": [
        "toyota", "honda", "ford", "chevrolet", "chevy", "tesla", "bmw", "audi", "volkswagen", "vw",
        "mercedes", "mercedes-benz", "porsche", "ferrari", "lamborghini", "nissan", "mazda", "subaru",
        "hyundai", "kia", "volvo", "jeep", "dodge", "ram 1500", "f-150",
        "model 3", "model y", "wrangler", "harley-davidson", "ducati", "yamaha", "kawasaki",
        "boeing 737", "airbus a320", "suv", "pickup truck", "hatchback", "minivan", "e-bike", "scooter",
    ],
    "food": [
        "gochujang", "miso", "tahini", "za'atar", "harissa", "sriracha", "panko", "mirin", "dashi",
        "sourdough", "focaccia", "risotto", "carbonara", "guanciale", "pecorino", "parmesan",
        "mozzarella", "ricotta", "prosciutto", "chorizo", "kimchi", "tofu", "tempeh", "quinoa",
        "couscous", "bulgur", "garam masala", "cumin", "turmeric", "paprika", "oregano", "thyme",
        "rosemary", "cilantro", "coriander", "baking soda", "baking powder", "yeast", "olive oil",
        "soy sauce", "fish sauce", "maple syrup", "vanilla extract", "cornstarch",
    ],
    "medical": ["covid-19", "covid", "sars-cov-2", "ibuprofen", "paracetamol", "acetaminophen", "insulin"],
}

CASED: dict[str, list[str]] = {
    "software": [
        "Python", "Rust", "Go", "Java", "Ruby", "Swift", "Perl", "R", "C", "Node", "Spring", "Express",
        "Flask", "Rails", "Chrome", "Firefox", "Safari", "Edge", "Excel", "Slack", "Jira", "Confluence",
        "Notion", "Figma", "Bun", "Yarn", "Cargo", "Vault", "Consul", "Spark", "Windows", "Apache",
        "Caddy", "Envoy", "Celery", "Jest", "Bootstrap", "Tailwind", "React", "Kernel",
    ],
    "product": ["Llama", "Mistral", "Grok"],
    "vehicle": ["Model S", "Model X", "Leaf", "Golf", "Beetle", "Accord", "Camry", "Prius", "Corolla", "Civic", "Mustang"],
}
# "Go", "R", "C", "Edge" are too ambiguous even capitalised at sentence start –
# they are only accepted when not the first token of a sentence (see pipeline).
SENTENCE_START_AMBIGUOUS = {"Go", "R", "C", "Edge", "Spring", "Express", "Leaf", "Golf", "Node", "Swift"}


def parse_custom(spec: str) -> dict[str, list[str]]:
    """Parse 'category: term, term' lines from the settings textarea."""
    out: dict[str, list[str]] = {}
    for line in spec.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        cat, terms = line.split(":", 1)
        cat = cat.strip().lower().replace(" ", "_")
        out.setdefault(cat, []).extend(t.strip() for t in terms.split(",") if t.strip())
    return out
