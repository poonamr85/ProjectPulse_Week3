"""Launch with: python -m streamlit run app.py."""

from project_status_agent.config import PROJECT_ROOT, load_env


def main():
    # Resolve destinations before importing LangChain/LangSmith integrations.
    load_env(str(PROJECT_ROOT / ".env"))
    from project_status_agent.ui.reports import main as run_ui

    run_ui()


main()
