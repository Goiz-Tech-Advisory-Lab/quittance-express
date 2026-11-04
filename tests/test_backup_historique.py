"""Sauvegarde / restauration : l'historique suit, et une restauration ne déclenche aucun envoi."""
import json
from datetime import date

import pytest

from quittance import importer, paths, scheduler, service, store

TODAY = date(2026, 10, 20)
MOIS = "2026-10"


def automatique(bail):
    bail.auto = True
    return store.upsert_bail(bail)


def nouveau_pc(dump):
    """Machine vierge sur laquelle la sauvegarde passe par le même chemin JSON que l'interface."""
    dump = json.loads(json.dumps(dump))
    store.save_baux([])
    (paths.data_dir() / "historique.json").unlink(missing_ok=True)
    return importer.import_json(dump)


def reactiver(bail_id):
    return store.upsert_bail(service.bail_from({"auto": True}, store.get_bail(bail_id)))


def test_export_contient_historique(configured):
    box, bail = configured
    service.suspend(bail.id, MOIS)
    dump = importer.export_backup()
    assert dump["version"] == 2
    assert [e["statut"] for e in dump["historique"]] == ["suspendue"]


def test_restauration_coupe_l_automatique_et_n_envoie_rien(configured):
    box, bail = configured
    automatique(bail)
    r = nouveau_pc(importer.export_backup())
    restored = store.get_bail(bail.id)
    assert restored.auto is False and restored.auto_a_reactiver is True and r["auto_a_reactiver"] == 1
    assert scheduler.due(TODAY) == []
    assert scheduler.run_due(TODAY)["envoyees"] == [] and box.messages == []


def test_quittance_deja_envoyee_ne_repart_pas_apres_reactivation(configured):
    box, bail = configured
    automatique(bail)
    assert scheduler.run_due(TODAY)["envoyees"] and len(box.messages) == 1
    r = nouveau_pc(importer.export_backup())
    assert r["historique"] == 1
    reactiver(bail.id)
    assert scheduler.due(TODAY) == []
    assert scheduler.run_due(TODAY)["envoyees"] == [] and len(box.messages) == 1


@pytest.mark.parametrize("mois", [MOIS, "2026-08"])
def test_loyer_non_recu_survit_a_la_restauration(configured, mois):
    box, bail = configured
    automatique(bail)
    service.suspend(bail.id, mois)
    nouveau_pc(importer.export_backup())
    reactiver(bail.id)
    assert scheduler.due(TODAY) == [] and box.messages == []
    if mois != MOIS:
        assert scheduler.blocked(TODAY)[0]["mois"] == [mois]


def test_reactivation_explicite_leve_l_alerte_et_relance_l_envoi(configured):
    box, bail = configured
    automatique(bail)
    nouveau_pc(importer.export_backup())
    assert reactiver(bail.id).auto_a_reactiver is False
    assert [b.id for b, _ in scheduler.due(TODAY)] == [bail.id]


def test_refuser_la_reactivation_leve_aussi_l_alerte(configured):
    _, bail = configured
    automatique(bail)
    nouveau_pc(importer.export_backup())
    saved = store.upsert_bail(service.bail_from({"auto": False}, store.get_bail(bail.id)))
    assert saved.auto is False and saved.auto_a_reactiver is False


def test_le_fichier_ne_peut_pas_pre_valider_l_automatique(configured):
    _, bail = configured
    dump = importer.export_backup()
    dump["baux"][0].update(auto=True, auto_a_reactiver=False)
    nouveau_pc(dump)
    assert store.get_bail(bail.id).auto is False


def test_ancienne_sauvegarde_sans_historique(configured):
    box, bail = configured
    automatique(bail)
    service.suspend(bail.id, "2026-08")
    dump = importer.export_backup()
    dump["version"] = 1
    del dump["historique"]
    r = importer.import_json(dump)                                          # l'historique local est conservé
    assert r["baux"] == 1 and r["historique_absent"] is True and r["historique"] == 0
    assert store.get_bail(bail.id).auto is False
    assert [e["mois"] for e in store.load_history()] == ["2026-08"]
    r = nouveau_pc(dump)                                                    # et sur une machine vierge
    assert r["historique_absent"] is True and store.load_history() == []
    assert scheduler.run_due(TODAY)["envoyees"] == [] and box.messages == []


def test_fusion_idempotente_et_l_existant_fait_foi(configured):
    _, bail = configured
    local = service.suspend(bail.id, "2026-08")
    dump = importer.export_backup()
    dump["historique"][0]["statut"] = "envoyee"                             # même id, valeur divergente
    dump["historique"].append({"id": "a" * 12, "date": "2026-01-05T09:00:00", "bail_id": bail.id, "mois": "2026-01",
                               "statut": "envoyee", "origine": "auto", "montant": 92050, "locataire": "H"})
    assert importer.import_json(dump)["historique"] == 1
    assert importer.import_json(dump)["historique"] == 0
    h = store.load_history()
    assert [e["id"] for e in h] == ["a" * 12, local["id"]]                  # ordre chronologique
    assert h[1]["statut"] == "suspendue"


def test_historique_hostile_est_filtre(configured):
    _, bail = configured
    ok = {"id": "b" * 12, "date": "2026-10-10T09:00:00+02:00", "bail_id": bail.id, "mois": MOIS, "statut": "envoyee",
          "origine": "<x>", "montant": True, "locataire": "L" * 9000, "inconnu": "x", "fichier": 12}
    dump = importer.export_backup()
    dump["historique"] = [ok, "x", None, {**ok, "id": "<img>"}, {**ok, "id": "c" * 12, "bail_id": "../x"},
                          {**ok, "id": "d" * 12, "mois": "2026-13"}, {**ok, "id": "e" * 12, "statut": "autre"},
                          {**ok, "id": "f" * 12, "date": "hier"}, {**ok, "id": "0" * 12, "date": None}]
    r = importer.import_json(dump)
    assert r["historique"] == 1 and r["historique_rejete"] == 8
    e = store.load_history()[0]
    assert e["date"] == "2026-10-10T09:00:00" and e["origine"] == "manuel" and len(e["locataire"]) == 400
    assert not {"montant", "inconnu", "fichier"} & set(e)
    from quittance.server import Api
    assert Api().state({})["historique"]                                    # l'interface se charge


@pytest.mark.parametrize("valeur", ["x", {"a": 1}, None, 3])
def test_champ_historique_invalide_ne_bloque_pas(configured, valeur):
    _, bail = configured
    dump = importer.export_backup()
    dump["historique"] = valeur
    r = importer.import_json(dump)
    assert r["baux"] == 1 and r["historique_absent"] is True


def test_historique_corrompu_repare_par_la_restauration(configured):
    box, bail = configured
    absent = store.upsert_bail(store.Bail(loc_nom="Hors sauvegarde", loc_email="x@test.fr", accord_email=True,
                                          auto=True, debut="2026-01"))
    automatique(bail)
    service.suspend(bail.id, "2026-08")
    dump = importer.export_backup()
    dump["baux"] = [b for b in dump["baux"] if b["id"] == bail.id]
    h = paths.data_dir() / "historique.json"
    h.write_text("{ cassé", encoding="utf-8")
    with pytest.raises(store.StoreError) as e:
        store.load_history()
    assert "supprimez" not in str(e.value)

    r = importer.import_json(dump)

    assert r["corrompus"] == ["historique.json"] and r["historique"] == 1
    assert [p.read_text(encoding="utf-8") for p in paths.data_dir().glob("historique.json.corrompu-*")] == ["{ cassé"]
    assert store.get_bail(absent.id).auto is False and store.get_bail(absent.id).auto_a_reactiver is True
    assert scheduler.run_due(TODAY)["envoyees"] == [] and box.messages == []


def test_baux_corrompus_repares_par_la_restauration(configured):
    _, bail = configured
    dump = importer.export_backup()
    (paths.data_dir() / "baux.json").write_text("[ cassé", encoding="utf-8")
    assert importer.import_json(dump)["corrompus"] == ["baux.json"]
    assert store.get_bail(bail.id).loc_email == "helene@test.fr"


def test_fichier_temporairement_inaccessible_n_est_pas_mis_de_cote(configured, monkeypatch):
    _, bail = configured
    service.suspend(bail.id, "2026-08")
    dump = importer.export_backup()
    real, target = store._read, "historique.json"

    def occupe(name, default):
        if name == target:
            raise store.StoreError("inaccessible")
        return real(name, default)

    with monkeypatch.context() as m:
        m.setattr(store, "_read", occupe)
        with pytest.raises(store.StoreError):
            importer.import_json(dump)
    assert not list(paths.data_dir().glob("*.corrompu-*"))
    assert len(store.load_history()) == 1


def test_sauvegarde_refusee_si_historique_illisible(configured):
    (paths.data_dir() / "historique.json").write_text("{ cassé", encoding="utf-8")
    with pytest.raises(store.StoreError):
        importer.export_backup()
