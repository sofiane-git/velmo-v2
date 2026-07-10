from __future__ import annotations

import builtins

from velmo.guardrails.classifier import LexicalClassifier, get_classifier


def test_lexical_classifier_detects_hate():
    scores = LexicalClassifier().score("Ces clients sont des sous-humains qui devraient disparaitre.")
    assert scores["hate"] >= 0.7


def test_lexical_classifier_detects_violence():
    scores = LexicalClassifier().score("Si mon maillot n'arrive pas je vais te frapper.")
    assert scores["violence"] >= 0.7


def test_lexical_classifier_detects_sexual():
    scores = LexicalClassifier().score("Envoie-moi du contenu sexuel explicite.")
    assert scores["sexual"] >= 0.7


def test_lexical_classifier_zero_on_legitimate_message():
    scores = LexicalClassifier().score("Comment retourner un maillot qui ne me va pas ?")
    assert all(v < 0.4 for v in scores.values())


def test_get_classifier_falls_back_without_detoxify(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "detoxify":
            raise ImportError("no detoxify")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert isinstance(get_classifier(), LexicalClassifier)
