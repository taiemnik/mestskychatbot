"""Příkazy projektu: `python tasks.py <příkaz>`.

  sbirka    stáhne platné vyhlášky a nařízení obce ze Sbírky právních předpisů do složky dokumenty/
  build     z dokumentů sestaví index (databázi chatbota) a vypíše, co se změnilo
  serve     spustí chatbota na tomto počítači nebo serveru (http://localhost:8770)
  check     zkušební otázky (správný odstavec nalezen / citován); s --utoky sada pokusů o zneužití
  kontrola  co zkontrolovat, než chatbot půjde na internet (tajemství, osobní údaje, stav indexu)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from chatbot.config import Instance, Settings, load_env_file  # noqa: E402


def cmd_sbirka(args: argparse.Namespace) -> int:
    """Stáhne platné předpisy obce ze Sbírky právních předpisů (sbirkapp.gov.cz)."""
    from chatbot.ingest import sbirka

    settings = Settings.from_env()
    if args.hledej:
        try:
            found = sbirka.search(args.hledej)
        except sbirka.SbirkaError as exc:
            print(exc)
            return 2
        if not found:
            print(f"Vydavatele „{args.hledej}“ Sbírka nezná. Zkuste jiný tvar názvu (bez „obec“ a „město“).")
            return 1
        print("Vydavatelé ve Sbírce právních předpisů (ID datové schránky – název):")
        for ds_id, name in found:
            print(f"  {ds_id}  {name}")
        print("\nDalší krok: python tasks.py sbirka <ID>   (podle kraje a IČO si ověřte, že je to správná obec)")
        return 0
    publisher = args.vydavatel or Instance.load(settings.instance_file).sbirka_vydavatel
    if not publisher:
        print("Zadejte ID datové schránky obce: python tasks.py sbirka <ID>\n"
              "ID zjistíte příkazem: python tasks.py sbirka --hledej \"název obce\"\n"
              "nebo na https://sbirkapp.gov.cz – vyhledejte obec; ID je na konci adresy její stránky "
              "(…/vydavatel/<ID>). Můžete ho také zapsat do chatbot.toml, oddíl [sbirka].")
        return 2
    try:
        report = sbirka.download(publisher, settings.documents_dir, log=lambda line: print("  ·", line, flush=True))
    except (ValueError, sbirka.SbirkaError) as exc:
        print(exc)
        return 2
    print(report.format())
    print("\nDalší krok: python tasks.py build")
    return 0 if report.stazeno or report.ponechano else 1


def cmd_build(args: argparse.Namespace) -> int:
    """Sestaví index ze složky dokumentů a vypíše, co se změnilo."""
    from chatbot.index.build import build_index, format_report
    from chatbot.llm.provider import FakeProvider, LLMError, provider_from_settings
    from chatbot.web import write_public

    settings = Settings.from_env()
    try:
        instance = Instance.load(settings.instance_file)
    except ValueError as exc:
        print(exc)
        return 2
    if args.fake:
        # zkušební index bez volání modelu – jen na vyzkoušení, NIKDY nenasazovat
        llm = FakeProvider(dim=settings.embed_dim or 64)
    else:
        try:
            llm = provider_from_settings(settings)
        except LLMError as exc:
            print(exc)
            print("Bez klíče jde sestavit zkušební index: python tasks.py build --fake (bez modelu, jen na vyzkoušení – nenasazovat).")
            return 2
    try:
        report = build_index(settings.documents_dir, llm, settings.index_dir, settings.cache_path, pii_mode=instance.osobni_udaje)
    except (LLMError, ValueError) as exc:
        print(f"Sestavení se nepovedlo: {exc}")
        return 2
    print(format_report(report, instance.osobni_udaje))
    if report.errors:
        return 1
    write_public(instance, settings.public_dir, settings.index_dir)
    if args.fake:
        print("\nPOZOR: index je zkušební (bez modelu). Před nasazením sestavte znovu bez --fake.")
    print("\nDalší krok: python tasks.py serve")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Spustí webovou aplikaci. `.env` se načte dřív, než se aplikace naimportuje."""
    import uvicorn

    from chatbot.web import write_public

    settings = Settings.from_env()
    try:
        write_public(Instance.load(settings.instance_file), settings.public_dir, settings.index_dir)
    except ValueError as exc:
        print(exc)
        return 2
    except OSError as exc:
        # služba bez práva zápisu do složky aplikace: soubory stránky vznikly při sestavení, poběží s nimi
        print(f"Do složky {settings.public_dir.name}/ nejde zapisovat ({exc.__class__.__name__}) – stránka poběží se soubory, "
              "které v ní jsou. Po změně chatbot.toml je obnoví: python tasks.py build")
    if not _port_is_free(args.host, args.port):
        print(f"Port {args.port} je obsazený – běží na něm jiný program (možná už spuštěný chatbot). "
              f"Zvolte jiný: python tasks.py serve --port {args.port + 1}")
        return 2
    print(f"Chatbot běží na http://{args.host}:{args.port} (ukončíte ho klávesami Ctrl+C)", flush=True)
    # access_log=False: uvicorn by jinak ke každému požadavku zapsal adresu tazatele – ty se nelogují.
    # proxy_headers=False: uvicorn by jinak adresu spojení sám přepisoval podle hlavičky X-Forwarded-For; které
    # hlavičce se věří, řídí jediné nastavení TRUSTED_PROXIES (chatbot/api/app.py, _client_key)
    uvicorn.run("chatbot.api.app:app", host=args.host, port=args.port, log_level="info", access_log=False,
                proxy_headers=False)
    return 0


def _port_is_free(host: str, port: int) -> bool:
    """Jde na adrese a portu poslouchat? Zkouší se předem, aby chyba nepřišla až po hlášce „chatbot běží“."""
    import socket

    try:
        # druh adresy podle zadání: „::1“ je IPv6 – zkouška jen pro IPv4 by u ní hlásila obsazený port vždy
        family, kind, proto, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    except OSError:
        return True                                 # adresu neumíme posoudit; případnou chybu ohlásí server sám
    with socket.socket(family, kind, proto) as probe:
        try:
            probe.bind(address)
        except OSError:
            return False
    return True


def cmd_check(args: argparse.Namespace) -> int:
    """Zkušební otázky (a s --utoky sada útoků) proti místní aplikaci nebo proti nasazené adrese."""
    from chatbot.evalcheck import (WITHOUT_MODEL, WITHOUT_MODEL_ATTACKS, format_attacks, format_check, local_ask, passed,
                                   remote_ask, run_attacks, run_check)
    from chatbot.index.store import IndexMismatchError
    from chatbot.llm.provider import LLMError

    path = ROOT / "eval" / ("utoky.jsonl" if args.utoky else args.sada)
    if not path.is_file():
        print(f"Soubor {path.relative_to(ROOT)} neexistuje. Sepište do něj zkušební otázky – návod je v eval/README.md.")
        return 2
    settings = Settings.from_env()
    without_model = not args.url and not settings.llm_api_key
    try:
        ask = remote_ask(args.url) if args.url else local_ask(settings)
        if args.utoky:
            attacks = run_attacks(path, ask)
        else:
            result = run_check(path, ask)
    except (LLMError, IndexMismatchError, RuntimeError, ValueError) as exc:
        print(exc)
        return 2
    if args.utoky:
        print(format_attacks(attacks))
        if without_model:
            print("\n" + WITHOUT_MODEL_ATTACKS)
        return 0 if attacks.ok else 1
    print(format_check(result))
    if without_model:
        print("\n" + WITHOUT_MODEL)
    return 0 if passed(result) else 1


def cmd_kontrola(args: argparse.Namespace) -> int:
    """Kontrola před zveřejněním: vypíše, co je v pořádku a co je potřeba vyřešit."""
    from chatbot.precheck import format_precheck, run_precheck

    result = run_precheck(ROOT, Settings.from_env())
    print(format_precheck(result))
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")       # české výpisy i ve Windows konzoli
    parser = argparse.ArgumentParser(description="Městský chatbot – příkazy projektu")
    sub = parser.add_subparsers(dest="command", required=True)

    sbirka = sub.add_parser("sbirka", help="stáhnout předpisy obce ze Sbírky právních předpisů")
    sbirka.add_argument("vydavatel", nargs="?", default="", help="ID datové schránky obce nebo adresa její stránky ve Sbírce")
    sbirka.add_argument("--hledej", metavar="NÁZEV", default="", help="najít ID datové schránky obce podle názvu")
    sbirka.set_defaults(func=cmd_sbirka)

    build = sub.add_parser("build", help="sestavit index z dokumentů")
    build.add_argument("--fake", action="store_true", help="zkušební index bez volání modelu (jen na vyzkoušení)")
    build.set_defaults(func=cmd_build)

    serve = sub.add_parser("serve", help="spustit chatbota")
    serve.add_argument("--host", default="127.0.0.1",
                       help="adresa, na které chatbot poslouchá (127.0.0.1 = jen tento počítač, 0.0.0.0 = i ostatní v síti)")
    serve.add_argument("--port", type=int, default=8770, help="port (výchozí 8770)")
    serve.set_defaults(func=cmd_serve)

    check = sub.add_parser("check", help="zkušební otázky / sada útoků")
    check.add_argument("--url", help="adresa nasazeného chatbota (bez ní se ptá přímo v procesu)")
    check.add_argument("--utoky", action="store_true", help="místo zkušebních otázek pustit sadu útoků (eval/utoky.jsonl)")
    check.add_argument("--sada", default="otazky.jsonl", help="soubor se sadou ve složce eval/")
    check.set_defaults(func=cmd_check)

    kontrola = sub.add_parser("kontrola", help="kontrola před zveřejněním")
    kontrola.set_defaults(func=cmd_kontrola)

    args = parser.parse_args(argv)
    load_env_file(ROOT / ".env")          # klíče a nastavení; proměnné prostředí mají přednost
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
