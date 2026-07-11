from __future__ import annotations

from velmo.guardrails.judge import RuleBasedJudge, get_judge, load_scope_keywords


def test_rule_based_judge_detects_out_of_scope():
    result = RuleBasedJudge().evaluate("Combien vaut mon maillot Maradona 86 aujourd'hui ?")
    assert result["hors_role"] >= 0.7


def test_rule_based_judge_zero_on_legitimate_message():
    result = RuleBasedJudge().evaluate("Comment retourner un maillot qui ne me va pas ?")
    assert result["hors_role"] == 0.0
    assert result["manipulation"] == 0.0
    assert result["secret_interne"] == 0.0


def test_rule_based_judge_does_not_flag_authentic_certificate_question():
    # "authentiques" ne doit pas déclencher le mot-clé "authentifier".
    result = RuleBasedJudge().evaluate("Vos maillots sont-ils authentiques avec certificat ?")
    assert result["hors_role"] == 0.0


def test_load_scope_keywords_from_yaml():
    phrases = load_scope_keywords()
    assert ("combien", "vaut") in phrases


def test_get_judge_falls_back_without_azure_credentials(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    assert isinstance(get_judge(), RuleBasedJudge)
