(function () {
  'use strict';
  var TOKEN = new URLSearchParams(location.search).get('t') || '';
  var $ = function (id) { return document.getElementById(id); };
  var MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
  var S = { state: null, dashMonth: '', statuts: {}, editId: null, preview: { timer: 0, seq: 0 }, pm: null };

  /* ---------- utilitaires ---------- */
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function eur(c) { var e = Math.floor(Math.abs(c) / 100), r = Math.abs(c) % 100; return (c < 0 ? '-' : '') + String(e).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ',' + (r < 10 ? '0' : '') + r + ' €'; }
  function cents(v) { var s = String(v || '').replace(/[\s  €]/g, '').replace(',', '.'); if (s === '') return 0; var n = Number(s); return isFinite(n) && n >= 0 ? Math.round(n * 100) : NaN; }
  function monthLabel(m) { var p = (m || '').split('-'); return p.length === 2 ? MOIS[+p[1] - 1] + ' ' + p[0] : ''; }
  function shiftMonth(m, d) { var p = m.split('-'), y = +p[0], mo = +p[1] - 1 + d; y += Math.floor(mo / 12); mo = ((mo % 12) + 12) % 12; return y + '-' + (mo < 9 ? '0' : '') + (mo + 1); }
  function fdate(iso) { if (!iso) return ''; var d = iso.slice(0, 10).split('-'); return d[2] + '/' + d[1] + '/' + d[0]; }
  function api(name, data) {
    return fetch('/api/' + name, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Token': TOKEN }, body: JSON.stringify(data || {}) })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (!j.ok) { var e = new Error(j.error || 'Erreur'); e.api = true; throw e; } return j; });
  }
  var toastT;
  function toast(msg, kind) { var t = $('toast'); t.textContent = msg; t.className = 'toast show ' + (kind || ''); clearTimeout(toastT); toastT = setTimeout(function () { t.className = 'toast'; }, msg.length > 80 ? 8000 : 4500); }
  function fail(e) { toast(e && e.message ? e.message : 'Erreur', 'err'); }
  function busy(btn, label) { var p = btn.textContent; btn.disabled = true; btn.textContent = label; return function () { btn.disabled = false; btn.textContent = p; }; }
  function ask(title, html, okLabel, danger) {
    return new Promise(function (resolve) {
      $('askTitle').textContent = title; $('askBody').innerHTML = html;
      var yes = $('askYes'); yes.textContent = okLabel || 'Confirmer'; yes.className = 'btn ' + (danger ? 'btn-danger' : 'btn-primary');
      $('askModal').hidden = false; yes.focus();
      function done(v) { $('askModal').hidden = true; yes.onclick = null; $('askNo').onclick = null; resolve(v); }
      yes.onclick = function () { done(true); }; $('askNo').onclick = function () { done(false); };
    });
  }

  /* ---------- navigation ---------- */
  function show(view) {
    document.querySelectorAll('#nav button').forEach(function (b) { b.classList.toggle('on', b.dataset.view === view); });
    document.querySelectorAll('.view').forEach(function (v) { v.classList.toggle('on', v.id === 'v-' + view); });
    if (view === 'baux') renderBaux();
  }
  $('nav').addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) show(b.dataset.view); });

  /* ---------- état ---------- */
  function load() {
    return api('state').then(function (s) {
      S.state = s;
      if (!S.dashMonth) S.dashMonth = s.mois;
      $('version').textContent = 'v' + s.version;
      $('navMailDot').hidden = s.mail_ok;
      fillBailleur(); fillSignature(); fillMail(); renderPlanif(); renderHist();
      return loadStatuts();
    }).catch(fail);
  }
  function loadStatuts() {
    return api('mois-status', { mois: S.dashMonth }).then(function (r) { S.statuts = r.statuts; renderDash(); renderBaux(); });
  }
  function bail(id) { return (S.state.baux || []).find(function (b) { return b.id === id; }); }

  /* ---------- tableau de bord ---------- */
  function renderDash() {
    var s = S.state; if (!s) return;
    $('dashMonth').value = S.dashMonth;
    $('dashMonthLabel').textContent = monthLabel(S.dashMonth);
    var al = [];
    if (!s.bailleur.nom) al.push(['Renseignez <b>le bailleur</b> (nom, adresse) qui figure sur les quittances.', 'bailleur', 'Compléter']);
    if (!s.signature) al.push(['Ajoutez votre <b>signature</b> pour des quittances complètes.', 'bailleur', 'Ajouter']);
    if (!s.mail_ok) al.push(['<b>Messagerie non configurée</b> : l’envoi par email est indisponible.', 'mail', 'Configurer']);
    var autos = s.baux.filter(function (b) { return b.auto && b.actif; });
    if (autos.length && s.planif.available && !s.planif.installed) al.push(['<b>' + autos.length + ' bail(s) en envoi automatique</b> mais la planification Windows n’est pas activée.', 'auto', 'Activer']);
    (s.bloques || []).forEach(function (m) { al.push(['Envoi automatique <b>bloqué</b> pour <b>' + esc(m.locataire) + '</b> : loyer non reçu en ' + esc(m.mois.map(monthLabel).join(', ')) + '. Réactivez ce mois une fois le loyer reçu.', 'goto:' + m.mois[0], 'Voir']); });
    (s.manques || []).forEach(function (m) { al.push(['Quittance <b>' + esc(monthLabel(m.mois)) + '</b> de <b>' + esc(m.locataire) + '</b> non envoyée (mois passé).', 'goto:' + m.mois, 'Voir']); });
    $('alerts').innerHTML = al.map(function (a) { return '<div class="alert"><span>' + a[0] + '</span><button class="btn btn-sm btn-ghost" data-go="' + esc(a[1]) + '">' + a[2] + '</button></div>'; }).join('');

    var active = s.baux.filter(function (b) { return b.actif; });
    if (!active.length) {
      $('dashList').innerHTML = '<div class="empty" style="grid-column:1/-1"><h3>Aucun bail pour l’instant</h3><p>Créez un bail par logement loué, ou importez les modèles exportés depuis goiztechadvisory.fr/quittance.</p>' +
        '<div class="row-gap"><button class="btn btn-primary" data-go="newbail">+ Nouveau bail</button><button class="btn btn-ghost" data-go="import">Importer (.json)</button></div></div>';
      return;
    }
    $('dashList').innerHTML = active.map(function (b) {
      var st = S.statuts[b.id], badge, acts = '';
      var isCurrent = S.dashMonth === s.mois;
      if (st && st.statut === 'envoyee') badge = '<span class="badge b-ok">✓ Envoyée le ' + fdate(st.date) + (st.origine === 'auto' ? ' (auto)' : '') + '</span>';
      else if (st && st.statut === 'suspendue') badge = '<span class="badge b-susp">⏸ Loyer non reçu — suspendue</span>';
      else if (b.auto) badge = '<span class="badge b-auto">⟳ Envoi auto le ' + b.jour_envoi_effectif + (isCurrent ? '' : ' (mois en cours)') + '</span>';
      else badge = '<span class="badge b-todo">À envoyer</span>';
      acts += '<button class="btn btn-sm btn-ghost" data-act="preview" data-id="' + esc(b.id) + '">Aperçu</button>';
      acts += '<button class="btn btn-sm btn-ghost" data-act="pdf" data-id="' + esc(b.id) + '">PDF</button>';
      if (!st || st.statut !== 'suspendue') acts += '<button class="btn btn-sm btn-teal" data-act="send" data-id="' + esc(b.id) + '">' + (st && st.statut === 'envoyee' ? 'Renvoyer' : 'Envoyer') + '</button>';
      if (!st) acts += '<button class="btn btn-sm btn-amber" data-act="suspend" data-id="' + esc(b.id) + '">Loyer non reçu</button>';
      if (st && st.statut === 'suspendue') acts += '<button class="btn btn-sm btn-ghost" data-act="unsuspend" data-id="' + esc(b.id) + '">Réactiver</button>';
      return '<article class="bcard"><div class="top"><div><div class="who">' + esc(b.locataire || 'Sans nom') + '</div><div class="addr">' + esc(b.logement_adresse) + '</div></div>' +
        '<div class="amt">' + eur(b.total) + '</div></div>' + badge + '<div class="acts">' + acts + '</div></article>';
    }).join('');
  }
  document.addEventListener('click', function (e) {
    var g = e.target.closest('[data-go]');
    if (g) {
      var v = g.dataset.go;
      if (v === 'newbail') { show('baux'); editBail(null); }
      else if (v === 'import') { show('baux'); $('importFile').click(); }
      else if (v.indexOf('goto:') === 0) { S.dashMonth = v.slice(5); loadStatuts(); }
      else show(v);
    }
  });
  $('dashList').addEventListener('click', function (e) {
    var b = e.target.closest('[data-act]'); if (!b) return;
    var id = b.dataset.id, act = b.dataset.act, m = S.dashMonth, bl = bail(id);
    if (act === 'preview') return openPreview(id, m);
    if (act === 'pdf') { var d0 = busy(b, '…'); return api('generate', { id: id, mois: m }).then(function (r) { d0(); toast('PDF enregistré : ' + r.fichier, 'ok'); load(); }, function (e) { d0(); fail(e); }); }
    if (act === 'send') return sendFlow(id, m, b);
    if (act === 'suspend') {
      return ask('Loyer non reçu', '<p>La quittance de <b>' + esc(monthLabel(m)) + '</b> pour <b>' + esc(bl.locataire) + '</b> ne sera pas envoyée (ni manuellement, ni automatiquement) tant que vous ne réactivez pas ce mois.</p>', 'Suspendre ce mois')
        .then(function (ok) { if (ok) api('suspend', { id: id, mois: m }).then(function () { toast('Mois suspendu.', 'ok'); load(); }, fail); });
    }
    if (act === 'unsuspend') api('unsuspend', { id: id, mois: m }).then(function () { toast('Mois réactivé.', 'ok'); load(); }, fail);
  });
  function sendFlow(id, m, btn) {
    var bl = bail(id), st = S.statuts[id], s = S.state;
    if (!s.mail_ok) { toast('Configurez d’abord la messagerie.', 'err'); show('mail'); return; }
    if (!bl.loc_email) { toast('Ajoutez l’email du locataire dans la fiche du bail.', 'err'); show('baux'); editBail(id); return; }
    if (!bl.accord_email) { toast('Cochez l’accord du locataire pour l’envoi par email (fiche du bail).', 'err'); show('baux'); editBail(id); return; }
    var again = st && st.statut === 'envoyee';
    var cc = s.bailleur.copie_bailleur && s.bailleur.email ? ' (copie à ' + esc(s.bailleur.email) + ')' : '';
    ask(again ? 'Renvoyer la quittance ?' : 'Envoyer la quittance ?',
      '<p>Quittance de <b>' + esc(monthLabel(m)) + '</b> — ' + eur(bl.total) + '<br>à <b>' + esc(bl.loc_email) + '</b>' + cc + '.</p>' +
      (again ? '<p>Elle a déjà été envoyée le ' + fdate(st.date) + '.</p>' : '') +
      '<p class="hint">Une quittance atteste que le loyer a été reçu.</p>', again ? 'Renvoyer' : 'Envoyer')
      .then(function (ok) {
        if (!ok) return;
        var d = btn ? busy(btn, 'Envoi…') : function () {};
        api('send', { id: id, mois: m, force: again }).then(function () { d(); toast('Quittance envoyée à ' + bl.loc_email + '.', 'ok'); $('previewModal').hidden = true; load(); }, function (e) { d(); fail(e); load(); });
      });
  }
  function openPreview(id, m) {
    S.pm = { id: id, mois: m };
    $('pmTitle').textContent = (bail(id).locataire || '') + ' — ' + monthLabel(m);
    $('pmImg').removeAttribute('src');
    $('previewModal').hidden = false;
    api('preview', { id: id, mois: m }).then(function (r) { $('pmImg').src = r.image; }, fail);
  }
  $('pmPdf').addEventListener('click', function () { var d = busy(this, '…'); api('generate', S.pm).then(function (r) { d(); toast('PDF enregistré : ' + r.fichier, 'ok'); load(); }, function (e) { d(); fail(e); }); });
  $('pmSend').addEventListener('click', function () { sendFlow(S.pm.id, S.pm.mois, this); });
  document.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { b.closest('.modal').hidden = true; }); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { $('previewModal').hidden = true; if (!$('askModal').hidden) $('askNo').click(); } });
  $('mPrev').addEventListener('click', function () { S.dashMonth = shiftMonth(S.dashMonth, -1); loadStatuts(); });
  $('mNext').addEventListener('click', function () { S.dashMonth = shiftMonth(S.dashMonth, 1); loadStatuts(); });
  $('dashMonth').addEventListener('change', function () { if (this.value) { S.dashMonth = this.value; loadStatuts(); } });

  /* ---------- baux ---------- */
  function days(sel, zeroLabel) { var h = zeroLabel ? '<option value="0">' + zeroLabel + '</option>' : ''; for (var d = 1; d <= 31; d++) h += '<option value="' + d + '">' + (d === 1 ? '1er' : d) + '</option>'; sel.innerHTML = h; }
  days($('jourPaiement'), '— Non précisé —'); days($('jourEnvoi'));
  function renderBaux() {
    var s = S.state; if (!s) return;
    $('bauxList').innerHTML = s.baux.length ? s.baux.map(function (b) {
      var st = S.statuts[b.id];
      return '<div class="li' + (b.id === S.editId ? ' sel' : '') + '" data-id="' + esc(b.id) + '"><div><div class="n">' + esc(b.locataire || 'Sans nom') + (b.actif ? '' : ' <span class="pill">terminé</span>') + '</div><div class="m">' + esc((b.logement_adresse || '').split('\n')[0]) + '</div></div>' +
        '<div class="r"><div class="n">' + eur(b.total) + '</div><div class="m">' + (b.auto ? '⟳ auto le ' + b.jour_envoi_effectif : 'manuel') + (st && st.statut === 'envoyee' ? ' · ✓ ' + esc(monthLabel(S.dashMonth)) : '') + '</div></div></div>';
    }).join('') : '<p class="hint">Aucun bail. Cliquez sur « + Nouveau bail ».</p>';
    $('previewMonthLabel').textContent = monthLabel(S.dashMonth);
  }
  $('bauxList').addEventListener('click', function (e) { var li = e.target.closest('.li'); if (li) editBail(li.dataset.id); });
  $('newBail').addEventListener('click', function () { editBail(null); });
  function editBail(id) {
    var f = $('bailForm'), b = id ? bail(id) : null;
    S.editId = id; f.hidden = false; f.reset();
    $('bailFormTitle').textContent = b ? 'Bail — ' + (b.locataire || '') : 'Nouveau bail';
    f.id.value = id || '';
    var v = b || { loc_prenom: '', loc_nom: '', loc_email: '', logement_adresse: '', loyer: 0, charges: 0, mode_paiement: '', jour_paiement: 0, debut: S.state.mois, auto: false, jour_envoi: 10, actif: true, accord_email: false, type_charges: 'provision' };
    ['loc_prenom', 'loc_nom', 'loc_email', 'logement_adresse', 'mode_paiement', 'debut'].forEach(function (k) { f[k].value = v[k] || ''; });
    f.loyer.value = v.loyer ? (v.loyer / 100).toFixed(2).replace('.', ',') : '';
    f.charges.value = v.charges ? (v.charges / 100).toFixed(2).replace('.', ',') : '';
    f.jour_paiement.value = String(v.jour_paiement || 0); f.jour_envoi.value = String(v.jour_envoi || 10);
    f.auto.checked = !!v.auto; f.actif.checked = v.actif !== false; f.accord_email.checked = !!v.accord_email;
    f.type_charges.value = v.type_charges === 'forfait' ? 'forfait' : 'provision';
    $('bailDelete').hidden = !id;
    document.querySelectorAll('#bailForm .invalid').forEach(function (x) { x.classList.remove('invalid'); });
    renderBaux(); bailChanged(); f.loc_prenom.focus();
  }
  function formBail() {
    var f = $('bailForm');
    return { id: f.id.value || undefined, loc_prenom: f.loc_prenom.value, loc_nom: f.loc_nom.value, loc_email: f.loc_email.value.trim(), logement_adresse: f.logement_adresse.value,
      loyer: f.loyer.value, charges: f.charges.value, mode_paiement: f.mode_paiement.value, jour_paiement: +f.jour_paiement.value, debut: f.debut.value,
      auto: f.auto.checked, jour_envoi: +f.jour_envoi.value, actif: f.actif.checked, accord_email: f.accord_email.checked, type_charges: f.type_charges.value };
  }
  function bailChanged() {
    var f = $('bailForm'), l = cents(f.loyer.value), c = cents(f.charges.value);
    f.loyer.classList.toggle('invalid', isNaN(l)); f.charges.classList.toggle('invalid', isNaN(c));
    $('bailTotal').textContent = eur((isNaN(l) ? 0 : l) + (isNaN(c) ? 0 : c));
    clearTimeout(S.preview.timer);
    S.preview.timer = setTimeout(refreshPreview, 280);
  }
  function refreshPreview() {
    if ($('bailForm').hidden) return;
    var b = formBail(); if (isNaN(cents(b.loyer)) || isNaN(cents(b.charges))) return;
    var seq = ++S.preview.seq;
    api('preview', { id: b.id, bail: b, mois: S.dashMonth }).then(function (r) {
      if (seq !== S.preview.seq) return;
      $('bailPreview').innerHTML = '<img alt="Aperçu" src="' + r.image + '">';
      $('bailWords').textContent = r.lettres;
    }).catch(function () {});
  }
  $('bailForm').addEventListener('input', function (e) { if (e.target.classList) e.target.classList.remove('invalid'); bailChanged(); });
  $('bailForm').accord_email.addEventListener('change', function () { if (!this.checked && $('bailForm').auto.checked) { $('bailForm').auto.checked = false; toast('Envoi automatique désactivé : il nécessite l’accord du locataire.', 'err'); } });
  $('bailForm').auto.addEventListener('change', function () {
    var cb = this; if (!cb.checked) return;
    var f = $('bailForm');
    if (!f.loc_email.value.trim() || !f.accord_email.checked) { cb.checked = false; toast('Renseignez l’email du locataire et cochez son accord pour l’envoi par email avant d’activer l’automatique.', 'err'); return; }
    ask('Activer l’envoi automatique ?',
      '<p>Chaque mois, à partir du <b>' + ($('bailForm').jour_envoi.value) + '</b>, la quittance de ce bail sera <b>envoyée automatiquement</b> au locataire, même si l’appli est fermée.</p>' +
      '<p><b>Attention :</b> une quittance atteste que le loyer a été payé. Si le loyer n’est pas arrivé, elle peut être utilisée contre vous. Pensez au bouton « Loyer non reçu » dans le tableau de bord en cas de retard.</p>', 'J’ai compris, activer')
      .then(function (ok) { if (!ok) cb.checked = false; });
  });
  $('bailForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var f = this, b = formBail(), miss = [];
    if (!b.loc_nom.trim()) miss.push(f.loc_nom); if (!b.logement_adresse.trim()) miss.push(f.logement_adresse);
    if (!(cents(b.loyer) > 0)) miss.push(f.loyer);
    if (miss.length) { miss.forEach(function (x) { x.classList.add('invalid'); }); toast('Complétez les champs obligatoires (*).', 'err'); return; }
    var btn = f.querySelector('[type=submit]'), d = busy(btn, 'Enregistrement…');
    api('bail-save', b).then(function (r) { d(); S.editId = r.bail.id; toast('Bail enregistré.', 'ok'); return load().then(function () { editBail(r.bail.id); }); }, function (e) { d(); fail(e); });
  });
  $('bailCancel').addEventListener('click', function () { $('bailForm').hidden = true; S.editId = null; renderBaux(); $('bailPreview').innerHTML = '<div class="paper-empty">Sélectionnez ou créez un bail pour voir la quittance.</div>'; });
  $('bailDelete').addEventListener('click', function () {
    var b = bail(S.editId); if (!b) return;
    ask('Supprimer ce bail ?', '<p>Le bail de <b>' + esc(b.locataire) + '</b> sera supprimé. Les PDF déjà archivés restent dans vos Documents.</p>', 'Supprimer', true)
      .then(function (ok) { if (ok) api('bail-delete', { id: b.id }).then(function () { toast('Bail supprimé.', 'ok'); $('bailCancel').click(); load(); }, fail); });
  });
  $('importBtn').addEventListener('click', function () { $('importFile').click(); });
  $('importFile').addEventListener('change', function () {
    var file = this.files && this.files[0]; this.value = ''; if (!file) return;
    var r = new FileReader();
    r.onload = function () { api('import-data', { text: r.result }).then(function (x) { toast(x.baux + ' bail(s) importé(s).', 'ok'); load(); }, fail); };
    r.readAsText(file);
  });

  /* ---------- bailleur & signature ---------- */
  function fillBailleur() {
    var f = $('bailleurForm'), b = S.state.bailleur;
    if (f.contains(document.activeElement)) return;
    ['nom', 'adresse', 'email', 'ville'].forEach(function (k) { f[k].value = b[k] || ''; });
    f.copie_bailleur.checked = b.copie_bailleur !== false;
  }
  $('bailleurForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var f = this; if (!f.nom.value.trim()) { f.nom.classList.add('invalid'); toast('Le nom du bailleur est obligatoire.', 'err'); return; }
    var d = busy(f.querySelector('[type=submit]'), 'Enregistrement…');
    api('bailleur', { nom: f.nom.value, adresse: f.adresse.value, email: f.email.value.trim(), ville: f.ville.value, copie_bailleur: f.copie_bailleur.checked })
      .then(function () { d(); toast('Bailleur enregistré.', 'ok'); load(); }, function (e) { d(); fail(e); });
  });
  function fillSignature() {
    var s = S.state.signature;
    $('sigBox').innerHTML = s ? '<img alt="Signature" src="' + s + '">' : '<span>Aucune signature</span>';
    $('sigDelete').hidden = !s;
  }
  $('sigBtn').addEventListener('click', function () { $('sigFile').click(); });
  $('sigFile').addEventListener('change', function () {
    var file = this.files && this.files[0]; this.value = ''; if (!file) return;
    if (file.size > 10 * 1024 * 1024) { toast('Image trop lourde (10 Mo max).', 'err'); return; }
    var r = new FileReader();
    r.onload = function () { api('signature', { data: r.result, transparent: $('sigTransparent').checked }).then(function () { toast('Signature enregistrée.', 'ok'); load(); }, fail); };
    r.readAsDataURL(file);
  });
  $('sigDelete').addEventListener('click', function () { api('signature-delete').then(function () { toast('Signature retirée.', 'ok'); load(); }, fail); });
  $('exportBtn').addEventListener('click', function () { var d = busy(this, 'Export…'); api('export-data').then(function (r) { d(); toast('Sauvegarde enregistrée : ' + r.fichier, 'ok'); }, function (e) { d(); fail(e); }); });

  /* ---------- messagerie ---------- */
  var mailInit = false;
  function fillMail() {
    var s = S.state, b = s.bailleur, sel = $('mailProvider');
    if (!mailInit) {
      sel.innerHTML = '<option value="">— Choisir —</option>' + Object.keys(s.presets).map(function (k) { return '<option value="' + k + '">' + esc(s.presets[k].label) + '</option>'; }).join('');
      mailInit = true;
    }
    if (!$('v-mail').contains(document.activeElement)) {
      sel.value = b.mail_provider || '';
      $('smtpHost').value = b.smtp_host || ''; $('smtpPort').value = b.smtp_port || ''; $('smtpSecurity').value = b.smtp_security || 'ssl';
      $('smtpUser').value = b.smtp_user || ''; $('fromEmail').value = b.from_email || ''; $('msClientId').value = b.ms_client_id || '';
      $('smtpPwd').value = '';
    }
    $('pwdState').textContent = s.has_password ? 'enregistré' : 'non enregistré'; $('pwdState').className = 'pill ' + (s.has_password ? 'ok' : 'warn');
    $('mailState').textContent = s.mail_ok ? '✓ Messagerie prête' : 'Non configurée'; $('mailState').className = 'pill ' + (s.mail_ok ? 'ok' : 'warn');
    $('msAccount').textContent = b.ms_account ? 'Connecté : ' + b.ms_account : 'Non connecté'; $('msAccount').className = 'pill ' + (b.ms_account ? 'ok' : 'warn');
    $('msLogout').hidden = !b.ms_account;
    $('msClientHint').textContent = s.ms_default_client ? 'Fourni avec l’appli. À renseigner seulement si vous utilisez votre propre inscription Microsoft.' : 'Obligatoire : identifiant de l’application Microsoft « Quittance Express » (voir le README).';
    providerChanged();
  }
  function providerChanged() {
    var p = $('mailProvider').value, pre = S.state.presets[p];
    $('mailHelp').textContent = pre ? pre.help : 'Choisissez votre fournisseur de messagerie.';
    $('smtpFields').hidden = !p || p === 'outlook'; $('msFields').hidden = p !== 'outlook'; $('customFields').hidden = p !== 'custom';
  }
  $('mailProvider').addEventListener('change', providerChanged);
  function mailPayload() {
    return { provider: $('mailProvider').value, host: $('smtpHost').value, port: $('smtpPort').value, security: $('smtpSecurity').value,
      user: $('smtpUser').value.trim(), from_email: $('fromEmail').value.trim(), password: $('smtpPwd').value, ms_client_id: $('msClientId').value.trim() };
  }
  $('mailSave').addEventListener('click', function () {
    var d = busy(this, 'Enregistrement…');
    api('mail-config', mailPayload()).then(function (r) { d(); toast(r.mail_ok ? 'Messagerie enregistrée.' : 'Enregistré. Complétez la configuration (mot de passe ou connexion Microsoft).', r.mail_ok ? 'ok' : 'err'); load(); }, function (e) { d(); fail(e); });
  });
  $('mailTest').addEventListener('click', function () {
    var btn = this, d = busy(btn, 'Envoi…');
    api('mail-config', mailPayload()).then(function () { return api('mail-test', {}); })
      .then(function (r) { d(); toast('Email de test envoyé à ' + r.to + '. Vérifiez votre boîte (et les spams).', 'ok'); load(); }, function (e) { d(); fail(e); load(); });
  });
  var msPoll = 0;
  $('msLogin').addEventListener('click', function () {
    var btn = this;
    clearInterval(msPoll);
    api('mail-config', mailPayload()).then(function () { return api('ms-login'); }).then(function () {
      btn.textContent = 'Connexion en cours… (cliquer pour relancer)';
      toast('Connectez-vous dans la fenêtre du navigateur qui vient de s’ouvrir. En cas de souci avec la clé d’accès, choisissez « Autres moyens de se connecter ».', 'ok');
      var start = Date.now();
      msPoll = setInterval(function () {
        api('ms-status').then(function (r) {
          if (r.state === 'ok') { clearInterval(msPoll); btn.textContent = 'Se connecter avec Microsoft'; toast('Connecté : ' + r.account, 'ok'); load(); }
          else if (r.state === 'error') { clearInterval(msPoll); btn.textContent = 'Se connecter avec Microsoft'; toast(r.error || 'Connexion Microsoft impossible.', 'err'); }
          else if (Date.now() - start > 330000) { clearInterval(msPoll); btn.textContent = 'Se connecter avec Microsoft'; toast('Connexion expirée : réessayez.', 'err'); }
        }).catch(function () {});
      }, 1500);
    }, fail);
  });
  $('msLogout').addEventListener('click', function () { api('ms-logout').then(function () { toast('Compte Microsoft déconnecté.', 'ok'); load(); }, fail); });

  /* ---------- envoi automatique ---------- */
  (function () { var h = ''; for (var i = 6; i <= 22; i++) h += '<option value="' + i + '">' + i + ' h 00</option>'; $('planifHour').innerHTML = h; $('planifHour').value = '9'; })();
  function renderPlanif() {
    var p = S.state.planif, el = $('planifState');
    if (!p.available) { el.textContent = 'Disponible uniquement sous Windows'; el.className = 'pill warn'; $('planifOn').hidden = true; $('planifOff').hidden = true; }
    else if (p.installed) { el.textContent = '✓ Planification active' + (p.next_run ? ' — prochaine vérification : ' + p.next_run : ''); el.className = 'pill ok'; $('planifOn').textContent = 'Mettre à jour'; $('planifOff').hidden = false; }
    else { el.textContent = 'Planification inactive'; el.className = 'pill warn'; $('planifOn').textContent = 'Activer la planification'; $('planifOff').hidden = true; }
    var autos = S.state.baux.filter(function (b) { return b.auto; });
    $('autoList').innerHTML = autos.length ? autos.map(function (b) {
      return '<div class="li" data-id="' + esc(b.id) + '"><div><div class="n">' + esc(b.locataire) + '</div><div class="m">' + esc(b.loc_email) + '</div></div><div class="r"><div class="n">le ' + b.jour_envoi_effectif + ' du mois</div><div class="m">' + eur(b.total) + (b.actif ? '' : ' · bail terminé') + '</div></div></div>';
    }).join('') : '<p class="hint">Aucun bail en envoi automatique. Activez-le dans la fiche d’un bail (« Baux & locataires »).</p>';
  }
  $('autoList').addEventListener('click', function (e) { var li = e.target.closest('.li'); if (li) { show('baux'); editBail(li.dataset.id); } });
  $('planifOn').addEventListener('click', function () { var d = busy(this, '…'); api('schedule-install', { hour: +$('planifHour').value }).then(function () { d(); toast('Planification activée.', 'ok'); load(); }, function (e) { d(); fail(e); }); });
  $('planifOff').addEventListener('click', function () { api('schedule-uninstall').then(function () { toast('Planification désactivée.', 'ok'); load(); }, fail); });
  $('runNow').addEventListener('click', function () {
    var d = busy(this, 'Vérification…');
    api('run-due').then(function (r) {
      d();
      var msg = r.ignore ? r.ignore : (r.envoyees.length ? r.envoyees.length + ' quittance(s) envoyée(s).' : 'Rien à envoyer pour le moment.');
      if (r.echecs.length) msg += ' Échecs : ' + r.echecs.join(' ; ');
      toast(msg, r.echecs.length ? 'err' : 'ok'); load();
    }, function (e) { d(); fail(e); });
  });

  /* ---------- historique ---------- */
  var LABELS = { envoyee: ['b-ok', 'Envoyée'], generee: ['b-todo', 'PDF généré'], suspendue: ['b-susp', 'Loyer non reçu'], echec: ['b-err', 'Échec'] };
  function renderHist() {
    var h = S.state.historique || [];
    $('histEmpty').hidden = h.length > 0;
    $('histTable').querySelector('tbody').innerHTML = h.map(function (e) {
      var l = LABELS[e.statut] || ['b-todo', e.statut];
      return '<tr><td>' + fdate(e.date) + '<small>' + esc((e.date || '').slice(11, 16)) + '</small></td><td>' + esc(e.locataire) + (e.destinataire ? '<small>' + esc(e.destinataire) + '</small>' : '') + '</td><td>' + esc(monthLabel(e.mois)) + '</td>' +
        '<td><span class="badge ' + l[0] + '">' + esc(l[1]) + '</span>' + (e.erreur ? '<small>' + esc(e.erreur) + '</small>' : '') + '</td><td>' + (e.origine === 'auto' ? 'automatique' : 'manuel') + '</td>' +
        '<td>' + (e.fichier ? '<button class="btn btn-sm btn-ghost" data-file="' + esc(e.fichier) + '">PDF</button>' : '') + '</td></tr>';
    }).join('');
  }
  $('histTable').addEventListener('click', function (e) { var b = e.target.closest('[data-file]'); if (b) api('open', { path: b.dataset.file }).catch(fail); });
  function openArchive(e) { if (e) e.preventDefault(); api('open', { what: 'archive' }).catch(fail); }
  $('openArchive').addEventListener('click', openArchive); $('openArchive2').addEventListener('click', openArchive);

  load();
})();
