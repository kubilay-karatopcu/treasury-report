import { useEffect, useRef, useState, useImperativeHandle, forwardRef } from 'react';
import {
  Search, Plus, Trash2, ChevronRight, ChevronDown,
} from 'lucide-react';
import { searchUsers, fetchDeptMembers } from '../lib/api.js';

/**
 * AudiencePicker — kullanıcının audience listesini kurmasını sağlar.
 *
 * Default state: kullanıcının departmanı bir grup olarak gelir, üyeleri
 * fetch edilir. Kullanıcı sicil arayarak ek kişiler ekleyebilir, gruptan
 * üye çıkarabilir, grubu silebilir.
 *
 * `ref.current.getResolvedSicils()` → flat sicil listesi döner (group remaining
 * members + individuals union).
 *
 * Props:
 *   - userInfo: { sicil, department } — default group için kullanılır
 *   - resetKey: bu değer her değiştiğinde state sıfırlanır (modal açılışı için)
 */
const AudiencePicker = forwardRef(function AudiencePicker(
  { userInfo, resetKey }, ref
) {
  // groups: [{ dept, members:[{sicil,name}], excluded:Set<sicil>, expanded:bool }]
  // individuals: [{ sicil, name, department }]
  const [groups, setGroups] = useState([]);
  const [individuals, setIndividuals] = useState([]);

  useEffect(() => {
    setIndividuals([]);
    if (userInfo?.department) {
      const dept = userInfo.department;
      fetchDeptMembers(dept)
        .then((members) => setGroups([{ dept, members, excluded: new Set(), expanded: false }]))
        .catch(() => setGroups([{ dept, members: [], excluded: new Set(), expanded: false }]));
    } else {
      setGroups([]);
    }
  }, [resetKey, userInfo?.department]);

  function toggleGroupExpand(idx) {
    setGroups((gs) => gs.map((g, i) => i === idx ? { ...g, expanded: !g.expanded } : g));
  }
  function removeGroup(idx) {
    setGroups((gs) => gs.filter((_, i) => i !== idx));
  }
  function removeMemberFromGroup(idx, sicil) {
    setGroups((gs) => gs.map((g, i) => {
      if (i !== idx) return g;
      const ex = new Set(g.excluded); ex.add(sicil);
      return { ...g, excluded: ex };
    }));
  }
  function addIndividual(user) {
    if (!user || !user.sicil) return;
    if (individuals.some((u) => u.sicil === user.sicil)) return;
    for (const g of groups) {
      if (g.dept === user.department && !g.excluded.has(user.sicil)) return;
    }
    setIndividuals((xs) => [...xs, user]);
  }
  function removeIndividual(sicil) {
    setIndividuals((xs) => xs.filter((u) => u.sicil !== sicil));
  }

  // Parent'a expose et: çözümlenmiş flat sicil listesi
  useImperativeHandle(ref, () => ({
    getResolvedSicils() {
      const set = new Set();
      for (const g of groups) {
        for (const m of g.members) {
          if (!g.excluded.has(m.sicil)) set.add(m.sicil);
        }
      }
      for (const u of individuals) set.add(u.sicil);
      return Array.from(set);
    },
  }), [groups, individuals]);

  const excludeSicils = new Set([
    ...individuals.map((u) => u.sicil),
    ...groups.flatMap((g) =>
      g.members.filter((m) => !g.excluded.has(m.sicil)).map((m) => m.sicil)),
  ]);

  return (
    <>
      <UserSearch onAdd={addIndividual} excludeSicils={excludeSicils} />
      <AudienceTable
        groups={groups}
        individuals={individuals}
        onToggleGroup={toggleGroupExpand}
        onRemoveGroup={removeGroup}
        onRemoveGroupMember={removeMemberFromGroup}
        onRemoveIndividual={removeIndividual}
      />
    </>
  );
});

export default AudiencePicker;


// ── Internal: search + table ────────────────────────────────────────────────

function UserSearch({ onAdd, excludeSicils }) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const debounceRef = useRef(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!q || q.trim().length < 2) {
      setResults([]); setOpen(false); return;
    }
    setBusy(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const items = await searchUsers(q.trim());
        setResults(items.filter((u) => !excludeSicils.has(u.sicil)));
        setOpen(true);
      } finally {
        setBusy(false);
      }
    }, 250);
    return () => clearTimeout(debounceRef.current);
  }, [q, excludeSicils]);

  function pick(user) {
    onAdd(user);
    setQ('');
    setResults([]);
    setOpen(false);
  }

  return (
    <div className="user-search">
      <div className="user-search-input-wrap">
        <Search size={14} strokeWidth={1.8} className="user-search-icon" />
        <input
          type="text"
          className="user-search-input"
          placeholder="İsim veya sicil ile ara…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => results.length && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
        />
        {busy && <span className="user-search-spin">…</span>}
      </div>
      {open && results.length > 0 && (
        <div className="user-search-dropdown">
          {results.map((u) => (
            <button
              key={u.sicil}
              type="button"
              className="user-search-result"
              onMouseDown={(e) => { e.preventDefault(); pick(u); }}
            >
              <div className="user-search-result-name">{u.name}</div>
              <div className="user-search-result-meta">
                <span>{u.sicil}</span>
                <span>·</span>
                <span>{u.department}</span>
              </div>
              <Plus size={14} strokeWidth={2} className="user-search-result-add" />
            </button>
          ))}
        </div>
      )}
      {open && !busy && results.length === 0 && q.trim().length >= 2 && (
        <div className="user-search-dropdown user-search-empty">Sonuç bulunamadı.</div>
      )}
    </div>
  );
}


function AudienceTable({
  groups, individuals,
  onToggleGroup, onRemoveGroup, onRemoveGroupMember, onRemoveIndividual,
}) {
  const empty = groups.length === 0 && individuals.length === 0;
  if (empty) {
    return (
      <div className="audience-empty">Henüz kimse eklenmedi. Yukarıdan arayıp ekle.</div>
    );
  }
  return (
    <div className="audience-table">
      {groups.map((g, gi) => {
        const remaining = g.members.filter((m) => !g.excluded.has(m.sicil));
        return (
          <div key={`g-${g.dept}`} className="audience-group">
            <div className="audience-row audience-row--group">
              <button
                type="button"
                className="audience-expand"
                onClick={() => onToggleGroup(gi)}
                disabled={g.members.length === 0}
                title={g.expanded ? 'Daralt' : 'Genişlet'}
              >
                {g.expanded
                  ? <ChevronDown size={14} strokeWidth={2} />
                  : <ChevronRight size={14} strokeWidth={2} />}
              </button>
              <div className="audience-group-info">
                <div className="audience-group-name">{g.dept}</div>
                <div className="audience-group-meta">
                  {remaining.length} kişi
                  {g.excluded.size > 0 && ` (${g.excluded.size} hariç)`}
                </div>
              </div>
              <button
                type="button"
                className="audience-remove"
                onClick={() => onRemoveGroup(gi)}
                title="Grubu listeden çıkar"
              >
                <Trash2 size={13} strokeWidth={1.8} />
              </button>
            </div>
            {g.expanded && (
              <div className="audience-members">
                {g.members.length === 0 && (
                  <div className="audience-member audience-member--empty">
                    Bu departmanda kayıtlı üye bulunamadı.
                  </div>
                )}
                {g.members.map((m) => {
                  const excluded = g.excluded.has(m.sicil);
                  return (
                    <div
                      key={m.sicil}
                      className={`audience-row audience-row--member${excluded ? ' is-excluded' : ''}`}
                    >
                      <span className="audience-row-spacer" />
                      <div className="audience-member-info">
                        <span className="audience-member-name">{m.name}</span>
                        <span className="audience-member-sicil">{m.sicil}</span>
                      </div>
                      {!excluded && (
                        <button
                          type="button"
                          className="audience-remove"
                          onClick={() => onRemoveGroupMember(gi, m.sicil)}
                          title="Bu üyeyi gruptan çıkar"
                        >
                          <Trash2 size={13} strokeWidth={1.8} />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {individuals.map((u) => (
        <div key={`i-${u.sicil}`} className="audience-row audience-row--individual">
          <span className="audience-row-spacer" />
          <div className="audience-member-info">
            <span className="audience-member-name">{u.name}</span>
            <span className="audience-member-sicil">
              {u.sicil}{u.department ? ` · ${u.department}` : ''}
            </span>
          </div>
          <button
            type="button"
            className="audience-remove"
            onClick={() => onRemoveIndividual(u.sicil)}
            title="Listeden çıkar"
          >
            <Trash2 size={13} strokeWidth={1.8} />
          </button>
        </div>
      ))}
    </div>
  );
}
