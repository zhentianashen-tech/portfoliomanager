(() => {
  const sequenceNode = document.querySelector('#sequence');
  const catalogNode = document.querySelector('#catalog');
  const catalogCount = document.querySelector('#catalog-count');
  const searchInput = document.querySelector('#catalog-search');
  const catalogHelp = document.querySelector('#catalog-help');
  const selectionBar = document.querySelector('#selection-bar');
  const selectionCopy = document.querySelector('#selection-copy');
  const removePhotoButton = document.querySelector('#empty-slot');
  const addBlockButton = document.querySelector('#add-block');
  const workspaceNode = document.querySelector('.workspace');
  const catalogToggleButton = document.querySelector('#catalog-toggle');
  const undoButton = document.querySelector('#undo');
  const redoButton = document.querySelector('#redo');
  const resetButton = document.querySelector('#reset');
  const restoreVersionSelect = document.querySelector('#restore-version');
  const saveButton = document.querySelector('#save');
  const changeCount = document.querySelector('#change-count');
  const saveStatus = document.querySelector('#save-status');
  const actionLogNode = document.querySelector('#action-log');
  const versionListNode = document.querySelector('#version-list');
  const toastNode = document.querySelector('#toast');

  let payload;
  let photoById;
  let drafts = { book: [], exhibition: [] };
  let baseDrafts = { book: [], exhibition: [] };
  let view = 'exhibition';
  let usageFilter = 'all';
  let selectionFilter = 'all';
  let selected = null;
  let undoStack = [];
  let redoStack = [];
  let actions = [];
  let savedSignature = '';
  let savedAt = '';
  let dragging = null;
  let toastTimer;
  let nextBlockNumber = 1;
  let catalogCollapsed = localStorage.getItem('sequence-editor:catalog-collapsed') === 'true';

  const roleFallback = ['Anchor', 'Echo I', 'Echo II', 'Bridge', 'Pause / surprise'];

  function escapeText(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
  }

  function pad(value, width = 2) { return String(value).padStart(width, '0'); }
  function deepCopy(value) { return JSON.parse(JSON.stringify(value)); }
  function signature(value = drafts) { return JSON.stringify(value); }
  function activeBlocks() { return drafts[view]; }
  function slotLimit() { return view === 'exhibition' ? 5 : 2; }
  function viewLabel() { return view === 'exhibition' ? 'Exhibition' : 'Book'; }
  function roleLabel(index) { return payload.roles[index]?.label || roleFallback[index] || `Slot ${index + 1}`; }
  function photoLabel(id) {
    if (!id) return 'Empty slot';
    const photo = photoById.get(id);
    return photo?.filename || photo?.caption || id;
  }
  function allPhotoIds(blocks = activeBlocks()) { return blocks.flatMap((block) => block.slots).filter(Boolean); }
  function usageForPhoto(id, bookUsed, exhibitionUsed) {
    const inBook = bookUsed.has(id);
    const inExhibition = exhibitionUsed.has(id);
    if (inBook && inExhibition) return { key: 'both', label: 'Book + Exhibition', inBook, inExhibition };
    if (inBook) return { key: 'book-only', label: 'Book only', inBook, inExhibition };
    if (inExhibition) return { key: 'exhibition-only', label: 'Exhibition only', inBook, inExhibition };
    return { key: 'unused-both', label: 'Unused in both', inBook, inExhibition };
  }
  function matchesUsageFilter(usage) {
    if (usageFilter === 'all') return true;
    if (usageFilter === 'unused-book') return !usage.inBook;
    if (usageFilter === 'unused-exhibition') return !usage.inExhibition;
    return usage.key === usageFilter;
  }
  function findBlock(blockId, blocks = activeBlocks()) { return blocks.find((block) => block.id === blockId); }
  function selectedSlot() {
    if (!selected) return null;
    const block = findBlock(selected.blockId);
    return block ? { block, id: block.slots[selected.slotIndex] } : null;
  }
  function snapshot() { return { drafts: deepCopy(drafts) }; }
  function blockPosition(blockId) { return activeBlocks().findIndex((block) => block.id === blockId); }

  function showToast(message) {
    clearTimeout(toastTimer);
    toastNode.textContent = message;
    toastNode.classList.add('visible');
    toastTimer = setTimeout(() => toastNode.classList.remove('visible'), 3600);
  }

  function setCatalogCollapsed(collapsed) {
    catalogCollapsed = collapsed;
    workspaceNode.classList.toggle('catalog-collapsed', collapsed);
    catalogToggleButton.textContent = collapsed ? 'Show catalog' : 'Hide catalog';
    catalogToggleButton.setAttribute('aria-expanded', String(!collapsed));
    localStorage.setItem('sequence-editor:catalog-collapsed', String(collapsed));
  }

  function saveBrowserDraft() {
    localStorage.setItem(`sequence-editor:${payload.baseSequenceSha256}`, JSON.stringify({
      schemaVersion: 3,
      updatedAt: new Date().toISOString(),
      drafts,
      actions,
    }));
  }

  function record(type, description, details, before, options = {}) {
    undoStack.push({ ...before, actionsLength: actions.length });
    redoStack = [];
    actions.push({ at: new Date().toISOString(), view, type, description, ...details });
    if (!options.keepSelection) selected = null;
    saveBrowserDraft();
    render();
  }

  function swapSlots(first, second) {
    if (!first || !second || (first.blockId === second.blockId && first.slotIndex === second.slotIndex)) return;
    const firstBlock = findBlock(first.blockId);
    const secondBlock = findBlock(second.blockId);
    if (!firstBlock || !secondBlock) return;
    const before = snapshot();
    const firstId = firstBlock.slots[first.slotIndex];
    const secondId = secondBlock.slots[second.slotIndex];
    [firstBlock.slots[first.slotIndex], secondBlock.slots[second.slotIndex]] = [secondId, firstId];
    record('swap', `Swapped ${viewLabel()} block ${blockPosition(first.blockId) + 1}, slot ${first.slotIndex + 1} (${photoLabel(firstId)}) with block ${blockPosition(second.blockId) + 1}, slot ${second.slotIndex + 1} (${photoLabel(secondId)}).`, {
      first: { blockId: first.blockId, slot: first.slotIndex + 1, photoId: firstId },
      second: { blockId: second.blockId, slot: second.slotIndex + 1, photoId: secondId },
    }, before);
  }

  function chooseCatalogPhoto(id) {
    const target = selectedSlot();
    if (!target) {
      showToast('Select a slot first.');
      return;
    }
    let existing = null;
    activeBlocks().some((block) => block.slots.some((slotId, slotIndex) => {
      if (slotId !== id) return false;
      existing = { blockId: block.id, slotIndex };
      return true;
    }));
    if (existing && existing.blockId === selected.blockId && existing.slotIndex === selected.slotIndex) {
      selected = null;
      render();
      return;
    }
    if (existing) {
      swapSlots(selected, existing);
      return;
    }
    const before = snapshot();
    const removedId = target.id;
    target.block.slots[selected.slotIndex] = id;
    const blockNumber = blockPosition(target.block.id) + 1;
    record(removedId ? 'replace-photo' : 'add-photo', removedId
      ? `Replaced ${photoLabel(removedId)} with ${photoLabel(id)} in ${viewLabel()} block ${blockNumber}, slot ${selected.slotIndex + 1}.`
      : `Added ${photoLabel(id)} to ${viewLabel()} block ${blockNumber}, slot ${selected.slotIndex + 1}.`, {
      blockId: target.block.id,
      block: blockNumber,
      slot: selected.slotIndex + 1,
      removedPhotoId: removedId,
      addedPhotoId: id,
    }, before);
  }

  function removePhotoAt(location) {
    const block = findBlock(location?.blockId);
    const removedId = block?.slots[location?.slotIndex];
    if (!block || !removedId) return;
    const before = snapshot();
    const blockNumber = blockPosition(block.id) + 1;
    block.slots[location.slotIndex] = null;
    record('remove-photo', `Returned ${photoLabel(removedId)} from ${viewLabel()} block ${blockNumber}, slot ${location.slotIndex + 1} to Unused and left the slot empty.`, {
      blockId: block.id,
      block: blockNumber,
      slot: location.slotIndex + 1,
      removedPhotoId: removedId,
    }, before);
  }

  function removeSelectedPhoto() {
    if (selected) removePhotoAt(selected);
  }

  function newBlock() {
    const kind = view === 'exhibition' ? 'exhibition-block' : 'book-spread';
    return {
      id: `${kind}-new-${Date.now()}-${nextBlockNumber++}`,
      title: view === 'exhibition' ? 'Untitled block' : 'Untitled spread',
      comment: '',
      slots: Array(slotLimit()).fill(null),
    };
  }

  function addBlock(afterBlockId = null) {
    const before = snapshot();
    const blocks = activeBlocks();
    const index = afterBlockId ? Math.max(0, blockPosition(afterBlockId) + 1) : blocks.length;
    const block = newBlock();
    blocks.splice(index, 0, block);
    record('add-block', `Added empty ${viewLabel()} ${view === 'exhibition' ? 'block' : 'spread'} at rank ${index + 1}.`, {
      blockId: block.id,
      rank: index + 1,
    }, before);
  }

  function removeBlock(blockId) {
    const blocks = activeBlocks();
    const index = blockPosition(blockId);
    if (index < 0) return;
    const before = snapshot();
    const [removed] = blocks.splice(index, 1);
    const photoCount = removed.slots.filter(Boolean).length;
    record('remove-block', `Removed ${viewLabel()} ${view === 'exhibition' ? 'block' : 'spread'} ${index + 1} (“${removed.title}”); ${photoCount} ${photoCount === 1 ? 'photo was' : 'photos were'} returned to Unused.`, {
      blockId,
      formerRank: index + 1,
      title: removed.title,
      removedPhotoIds: removed.slots.filter(Boolean),
    }, before);
  }

  function moveBlock(blockId, direction) {
    const blocks = activeBlocks();
    const from = blockPosition(blockId);
    const to = from + direction;
    if (from < 0 || to < 0 || to >= blocks.length) return;
    const before = snapshot();
    const [block] = blocks.splice(from, 1);
    blocks.splice(to, 0, block);
    record('move-block', `Moved ${viewLabel()} ${view === 'exhibition' ? 'block' : 'spread'} “${block.title}” from rank ${from + 1} to ${to + 1}.`, {
      blockId,
      from: from + 1,
      to: to + 1,
    }, before);
  }

  function updateBlockText(blockId, field, value) {
    const block = findBlock(blockId);
    if (!block || block[field] === value) return;
    const before = snapshot();
    const oldValue = block[field];
    block[field] = value;
    const blockNumber = blockPosition(blockId) + 1;
    const label = field === 'title' ? 'title' : 'comment';
    record(`edit-${label}`, `Changed the ${label} for ${viewLabel()} block ${blockNumber} from “${oldValue || '(empty)'}” to “${value || '(empty)'}”.`, {
      blockId,
      block: blockNumber,
      field,
      before: oldValue,
      after: value,
    }, before);
  }

  function cardMarkup(id, block, slotIndex, blockIndex) {
    const isSelected = selected?.blockId === block.id && selected?.slotIndex === slotIndex;
    const secondary = view === 'exhibition' ? roleLabel(slotIndex) : (slotIndex ? 'Right page' : 'Left page');
    if (!id) {
      return `<button class="photo-card empty${isSelected ? ' selected' : ''}" type="button" draggable="false" data-block-id="${escapeText(block.id)}" data-slot-index="${slotIndex}" aria-pressed="${isSelected}" title="Empty slot. Select it to add a photo.">
        <span class="photo-image" aria-hidden="true"></span>
        <span class="photo-copy">
          <span class="photo-position"><span>${pad(blockIndex + 1, 2)}.${slotIndex + 1}</span><span>${escapeText(secondary)}</span></span>
          <strong>Empty slot</strong>
          <small>Select to add a photo</small>
        </span>
      </button>`;
    }
    const photo = photoById.get(id);
    const isReplacement = !payload.order.includes(id);
    return `<div class="photo-card${isSelected ? ' selected' : ''}${isReplacement ? ' replacement' : ''}" role="button" tabindex="0" draggable="true" data-block-id="${escapeText(block.id)}" data-slot-index="${slotIndex}" aria-pressed="${isSelected}" aria-label="Slot ${blockIndex + 1}.${slotIndex + 1}: ${escapeText(photo.filename)}, ${escapeText(secondary)}, ${escapeText(photo.chapter)}">
      <span class="photo-image"><img src="${photo.thumb}" alt="${escapeText(photo.caption)}" loading="lazy" draggable="false"></span>
      <span class="slot-index">${pad(blockIndex + 1, 2)}.${slotIndex + 1}</span>
      <span class="photo-copy">
        <span class="photo-position"><span>${escapeText(secondary)}</span></span>
        <strong>${escapeText(photo.filename)}</strong>
        <small>${escapeText(photo.chapter)}</small>
      </span>
      <button class="slot-remove" type="button" data-photo-action="remove" aria-label="Remove ${escapeText(photo.filename)} and leave this slot empty" title="Remove photo and leave slot empty"><span aria-hidden="true">&times;</span></button>
    </div>`;
  }

  function renderSequence() {
    const blocks = activeBlocks();
    sequenceNode.dataset.view = view;
    if (!blocks.length) {
      sequenceNode.innerHTML = `<div class="sequence-empty"><strong>No ${view === 'exhibition' ? 'exhibition blocks' : 'book spreads'} yet.</strong><span>Use “Add ${view === 'exhibition' ? 'block' : 'spread'}” to begin.</span></div>`;
      return;
    }
    sequenceNode.innerHTML = blocks.map((block, blockIndex) => {
      const photoCount = block.slots.filter(Boolean).length;
      const kind = view === 'exhibition' ? 'Block' : 'Spread';
      return `<section class="sequence-group" data-block-id="${escapeText(block.id)}">
        <div class="group-meta">
          <span>${kind} ${pad(blockIndex + 1)} / ${pad(blocks.length)} · ${photoCount} / ${slotLimit()} photos</span>
          <label class="block-field block-title-field"><small>Main title</small><input class="block-title-input" type="text" maxlength="120" value="${escapeText(block.title)}" data-block-id="${escapeText(block.id)}" data-field="title"></label>
          <label class="block-field"><small>Comment</small><textarea class="block-comment-input" rows="3" maxlength="1200" data-block-id="${escapeText(block.id)}" data-field="comment" placeholder="Add a short note…">${escapeText(block.comment)}</textarea></label>
          <div class="block-actions" aria-label="${kind} ${blockIndex + 1} controls">
            <button type="button" data-block-action="up" data-block-id="${escapeText(block.id)}" ${blockIndex === 0 ? 'disabled' : ''} aria-label="Move ${kind.toLowerCase()} up">↑</button>
            <button type="button" data-block-action="down" data-block-id="${escapeText(block.id)}" ${blockIndex === blocks.length - 1 ? 'disabled' : ''} aria-label="Move ${kind.toLowerCase()} down">↓</button>
            <button type="button" data-block-action="add" data-block-id="${escapeText(block.id)}">Add after</button>
            <button class="remove-block" type="button" data-block-action="remove" data-block-id="${escapeText(block.id)}">Remove</button>
          </div>
        </div>
        <div class="group-photos">${block.slots.map((id, slotIndex) => cardMarkup(id, block, slotIndex, blockIndex)).join('')}</div>
      </section>`;
    }).join('');
  }

  function filteredPhotos() {
    const bookUsed = new Set(allPhotoIds(drafts.book));
    const exhibitionUsed = new Set(allPhotoIds(drafts.exhibition));
    const query = searchInput.value.trim().toLowerCase();
    return payload.photos.filter((photo) => {
      const usage = usageForPhoto(photo.id, bookUsed, exhibitionUsed);
      if (!matchesUsageFilter(usage)) return false;
      if (selectionFilter !== 'all' && photo.selection !== selectionFilter) return false;
      if (query && !`${photo.caption} ${photo.filename} ${photo.chapter} ${photo.sourcePath}`.toLowerCase().includes(query)) return false;
      return true;
    });
  }

  function renderCatalog() {
    const photos = filteredPhotos();
    const activeUsed = new Set(allPhotoIds());
    const bookUsed = new Set(allPhotoIds(drafts.book));
    const exhibitionUsed = new Set(allPhotoIds(drafts.exhibition));
    catalogCount.textContent = `${photos.length} / ${payload.photos.length}`;
    const target = selectedSlot();
    catalogHelp.textContent = !target
      ? `Select a ${viewLabel()} slot before choosing a photo.`
      : target.id
        ? `Replace ${photoLabel(target.id)}, or choose another used photo to swap.`
        : `Fill this empty ${viewLabel()} slot from the library.`;
    catalogNode.innerHTML = photos.length ? photos.map((photo) => {
      const usage = usageForPhoto(photo.id, bookUsed, exhibitionUsed);
      return `<button class="catalog-card${activeUsed.has(photo.id) ? ' in-sequence' : ''}" type="button" draggable="true" data-photo-id="${photo.id}" title="${escapeText(photo.caption)}">
        <img src="${photo.thumb}" alt="${escapeText(photo.caption)}" loading="lazy" draggable="false">
        <span>${escapeText(photo.filename)}</span>
        <small class="usage-badge usage-${usage.key}">${escapeText(usage.label)}</small>
        <small class="catalog-status">${escapeText(photo.selection || photo.chapter)}</small>
      </button>`;
    }).join('') : '<p class="catalog-empty">No photographs match these filters.</p>';
  }

  function renderActions() {
    const recent = actions.slice(-10).reverse();
    actionLogNode.innerHTML = recent.length ? recent.map((action) => `<li>${escapeText(action.description)}</li>`).join('') : '<li>No edits yet.</li>';
  }

  function renderStatus() {
    const changed = signature() !== savedSignature;
    changeCount.textContent = `${drafts.book.length} book spreads · ${drafts.exhibition.length} exhibition blocks${actions.length ? ` · ${actions.length} actions` : ''}`;
    resetButton.disabled = restoreVersionSelect.value === 'published' && signature() === signature(baseDrafts);
    undoButton.disabled = !undoStack.length;
    redoButton.disabled = !redoStack.length;
    addBlockButton.textContent = `Add ${view === 'exhibition' ? 'block' : 'spread'}`;
    selectionBar.hidden = !selected;
    const target = selectedSlot();
    removePhotoButton.hidden = !target?.id;
    if (target) {
      const blockNumber = blockPosition(target.block.id) + 1;
      selectionCopy.textContent = target.id
        ? `${viewLabel()} ${view === 'exhibition' ? 'block' : 'spread'} ${blockNumber}, slot ${selected.slotIndex + 1}: ${photoLabel(target.id)}.`
        : `${viewLabel()} ${view === 'exhibition' ? 'block' : 'spread'} ${blockNumber}, slot ${selected.slotIndex + 1} is empty.`;
    }
    if (changed && savedSignature) saveStatus.textContent = 'Unsaved changes since last version';
  }

  function render() {
    renderSequence();
    renderCatalog();
    renderActions();
    renderStatus();
  }

  function undo() {
    const entry = undoStack.pop();
    if (!entry) return;
    redoStack.push({ drafts: deepCopy(drafts), actions: deepCopy(actions) });
    drafts = entry.drafts;
    actions = actions.slice(0, entry.actionsLength);
    selected = null;
    saveBrowserDraft();
    render();
  }

  function redo() {
    const entry = redoStack.pop();
    if (!entry) return;
    undoStack.push({ drafts: deepCopy(drafts), actionsLength: actions.length });
    drafts = entry.drafts;
    actions = entry.actions;
    selected = null;
    saveBrowserDraft();
    render();
  }

  function restorePublished() {
    if (!confirm('Restore both published drafts? You can return to a saved version from Version history.')) return;
    const before = snapshot();
    drafts = deepCopy(baseDrafts);
    record('restore-published', 'Restored the published Book and Exhibition drafts.', {}, before);
  }

  async function loadHistory() {
    try {
      const response = await fetch('/api/history');
      if (!response.ok) throw new Error('Version history is unavailable.');
      const result = await response.json();
      const versions = result.versions || [];
      const selectedRestore = restoreVersionSelect.value;
      restoreVersionSelect.innerHTML = `<option value="published">Published baseline</option>${versions.map((version) => `<option value="${escapeText(version.file)}">${escapeText(version.sessionName || 'Saved version')} · ${escapeText(version.savedAt || '')}</option>`).join('')}`;
      if ([...restoreVersionSelect.options].some((option) => option.value === selectedRestore)) restoreVersionSelect.value = selectedRestore;
      versionListNode.innerHTML = versions.length ? versions.map((version) => `<li>
        <div><strong>${escapeText(version.sessionName || 'Saved version')}</strong><span>${escapeText(version.savedAt || '')}</span><small>${escapeText(version.summary || '')}</small></div>
        <button type="button" data-version-file="${escapeText(version.file)}">Load</button>
      </li>`).join('') : '<li class="version-empty">No saved versions yet.</li>';
      renderStatus();
    } catch (error) {
      versionListNode.innerHTML = `<li class="version-empty">${escapeText(error.message)}</li>`;
    }
  }

  function restoreSelectedVersion() {
    if (restoreVersionSelect.value === 'published') restorePublished();
    else loadVersion(restoreVersionSelect.value);
  }

  async function loadVersion(file) {
    try {
      const response = await fetch(`/api/version/${encodeURIComponent(file)}`);
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not load that version.');
      if (!usableDrafts(result.drafts)) throw new Error('That version does not contain a compatible editable draft.');
      const before = snapshot();
      drafts = result.drafts;
      record('load-version', `Loaded saved version “${result.sessionName || file}” from ${result.savedAt || 'version history'}.`, { sourceVersion: file }, before);
      saveStatus.textContent = `Loaded ${result.sessionName || file} · save to make it latest`;
      showToast('Version loaded. Save it to make it the latest version.');
    } catch (error) {
      showToast(error.message);
    }
  }

  async function saveVersion() {
    saveButton.disabled = true;
    saveButton.textContent = 'Saving…';
    try {
      const response = await fetch('/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sessionName: document.querySelector('#session-name').value.trim(),
          note: document.querySelector('#session-note').value.trim(),
          baseSequenceSha256: payload.baseSequenceSha256,
          drafts: { bookBlocks: drafts.book, exhibitionBlocks: drafts.exhibition },
          actions,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not save the version.');
      savedSignature = signature();
      savedAt = result.savedAt || new Date().toISOString();
      actions = [];
      undoStack = [];
      redoStack = [];
      localStorage.removeItem(`sequence-editor:${payload.baseSequenceSha256}`);
      saveStatus.textContent = `Latest version · ${result.sessionName || 'saved'}`;
      showToast(`Version saved: ${result.markdownPath}`);
      await loadHistory();
      renderStatus();
    } catch (error) {
      showToast(error.message);
    } finally {
      saveButton.disabled = false;
      saveButton.textContent = 'Save version';
    }
  }

  function makeBaseDrafts(order = payload.order, exhibitionEmpty = []) {
    const book = [];
    for (let start = 0; start < order.length; start += 2) {
      const leftMovement = payload.movements[Math.floor(start / 5)];
      const rightMovement = payload.movements[Math.floor(Math.min(start + 1, order.length - 1) / 5)];
      const comment = leftMovement?.title === rightMovement?.title ? leftMovement?.title : `${leftMovement?.title} → ${rightMovement?.title}`;
      book.push({
        id: `book-spread-${pad(book.length + 1, 3)}`,
        title: `Spread ${pad(book.length + 1)}`,
        comment: comment || '',
        slots: order.slice(start, start + 2),
      });
    }
    const exhibition = payload.movements.map((movement, index) => ({
      id: movement.id || `exhibition-block-${pad(index + 1, 3)}`,
      title: movement.title || `Block ${pad(index + 1)}`,
      comment: movement.note || '',
      slots: order.slice(index * 5, index * 5 + 5).map((id, offset) => exhibitionEmpty[index * 5 + offset] ? null : id),
    }));
    return { book, exhibition };
  }

  function usableBlocks(blocks, size) {
    if (!Array.isArray(blocks)) return false;
    const ids = new Set();
    const photos = new Set();
    return blocks.every((block) => {
      if (!block || typeof block.id !== 'string' || ids.has(block.id) || !Array.isArray(block.slots) || block.slots.length !== size) return false;
      ids.add(block.id);
      return block.slots.every((id) => {
        if (id == null) return true;
        if (!photoById.has(id) || photos.has(id)) return false;
        photos.add(id);
        return true;
      });
    });
  }
  function usableDrafts(value) { return value && usableBlocks(value.book, 2) && usableBlocks(value.exhibition, 5); }

  sequenceNode.addEventListener('click', (event) => {
    const photoAction = event.target.closest('[data-photo-action="remove"]');
    if (photoAction) {
      const card = photoAction.closest('.photo-card');
      removePhotoAt({ blockId: card.dataset.blockId, slotIndex: Number(card.dataset.slotIndex) });
      return;
    }
    const blockAction = event.target.closest('[data-block-action]');
    if (blockAction) {
      const blockId = blockAction.dataset.blockId;
      if (blockAction.dataset.blockAction === 'up') moveBlock(blockId, -1);
      if (blockAction.dataset.blockAction === 'down') moveBlock(blockId, 1);
      if (blockAction.dataset.blockAction === 'add') addBlock(blockId);
      if (blockAction.dataset.blockAction === 'remove') removeBlock(blockId);
      return;
    }
    const card = event.target.closest('.photo-card');
    if (!card) return;
    const next = { blockId: card.dataset.blockId, slotIndex: Number(card.dataset.slotIndex) };
    if (!selected) selected = next;
    else if (selected.blockId === next.blockId && selected.slotIndex === next.slotIndex) selected = null;
    else swapSlots(selected, next);
    render();
  });

  sequenceNode.addEventListener('keydown', (event) => {
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.photo-card')) {
      event.preventDefault();
      event.target.click();
    }
  });

  sequenceNode.addEventListener('change', (event) => {
    const field = event.target.closest('[data-field][data-block-id]');
    if (field) updateBlockText(field.dataset.blockId, field.dataset.field, field.value.trim());
  });

  sequenceNode.addEventListener('dragstart', (event) => {
    const card = event.target.closest('.photo-card');
    if (!card || card.classList.contains('empty') || event.target.closest('.slot-remove')) {
      event.preventDefault();
      return;
    }
    dragging = { kind: 'sequence', blockId: card.dataset.blockId, slotIndex: Number(card.dataset.slotIndex) };
    card.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', `${dragging.blockId}:${dragging.slotIndex}`);
  });
  sequenceNode.addEventListener('dragover', (event) => {
    const card = event.target.closest('.photo-card');
    if (!card) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    sequenceNode.querySelectorAll('.drop-target').forEach((node) => node.classList.remove('drop-target'));
    card.classList.add('drop-target');
  });
  sequenceNode.addEventListener('drop', (event) => {
    const card = event.target.closest('.photo-card');
    if (!card || !dragging) return;
    event.preventDefault();
    const target = { blockId: card.dataset.blockId, slotIndex: Number(card.dataset.slotIndex) };
    if (dragging.kind === 'catalog') {
      const photoId = dragging.photoId;
      dragging = null;
      selected = target;
      chooseCatalogPhoto(photoId);
      return;
    }
    swapSlots(dragging, target);
  });
  sequenceNode.addEventListener('dragend', () => {
    dragging = null;
    sequenceNode.querySelectorAll('.dragging, .drop-target').forEach((node) => node.classList.remove('dragging', 'drop-target'));
  });

  catalogNode.addEventListener('click', (event) => {
    const card = event.target.closest('.catalog-card');
    if (card) chooseCatalogPhoto(card.dataset.photoId);
  });
  catalogNode.addEventListener('dragstart', (event) => {
    const card = event.target.closest('.catalog-card');
    if (!card) return;
    dragging = { kind: 'catalog', photoId: card.dataset.photoId };
    card.classList.add('dragging');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', card.dataset.photoId);
  });
  catalogNode.addEventListener('dragend', () => {
    dragging = null;
    catalogNode.querySelectorAll('.dragging').forEach((node) => node.classList.remove('dragging'));
    sequenceNode.querySelectorAll('.drop-target').forEach((node) => node.classList.remove('drop-target'));
  });
  versionListNode.addEventListener('click', (event) => {
    const button = event.target.closest('[data-version-file]');
    if (button) loadVersion(button.dataset.versionFile);
  });

  document.querySelectorAll('.view-option').forEach((button) => button.addEventListener('click', () => {
    view = button.dataset.view;
    selected = null;
    document.querySelectorAll('.view-option').forEach((option) => {
      const active = option === button;
      option.classList.toggle('active', active);
      option.setAttribute('aria-pressed', String(active));
    });
    render();
  }));
  document.querySelectorAll('.filter').forEach((button) => button.addEventListener('click', () => {
    const group = button.closest('[data-filter-group]');
    if (group.dataset.filterGroup === 'usage') usageFilter = button.dataset.filter;
    else selectionFilter = button.dataset.filter;
    group.querySelectorAll('.filter').forEach((option) => {
      const active = option === button;
      option.classList.toggle('active', active);
      option.setAttribute('aria-pressed', String(active));
    });
    renderCatalog();
  }));

  searchInput.addEventListener('input', renderCatalog);
  addBlockButton.addEventListener('click', () => addBlock());
  catalogToggleButton.addEventListener('click', () => setCatalogCollapsed(!catalogCollapsed));
  removePhotoButton.addEventListener('click', removeSelectedPhoto);
  document.querySelector('#clear-selection').addEventListener('click', () => { selected = null; render(); });
  undoButton.addEventListener('click', undo);
  redoButton.addEventListener('click', redo);
  resetButton.addEventListener('click', restoreSelectedVersion);
  restoreVersionSelect.addEventListener('change', renderStatus);
  saveButton.addEventListener('click', saveVersion);
  window.addEventListener('keydown', (event) => {
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'z') return;
    event.preventDefault();
    if (event.shiftKey) redo(); else undo();
  });

  async function start() {
    try {
      setCatalogCollapsed(catalogCollapsed);
      const response = await fetch('/api/state');
      if (!response.ok) throw new Error('The local editor server could not load the sequence.');
      payload = await response.json();
      document.querySelector('#project-title').textContent = payload.title || 'Portfolio sequence';
      document.title = `Sequence desk · ${payload.title || 'Portfolio sequence'}`;
      photoById = new Map(payload.photos.map((photo) => [photo.id, photo]));
      baseDrafts = makeBaseDrafts();
      drafts = deepCopy(baseDrafts);
      if (usableDrafts(payload.latestDraft)) {
        drafts = payload.latestDraft;
        savedAt = payload.latestVersion?.savedAt || '';
        saveStatus.textContent = `Latest version · ${payload.latestVersion?.sessionName || 'saved draft'}`;
      }
      savedSignature = signature();
      const stored = JSON.parse(localStorage.getItem(`sequence-editor:${payload.baseSequenceSha256}`) || 'null');
      const storedIsNewer = stored?.updatedAt && (!savedAt || Date.parse(stored.updatedAt) > Date.parse(savedAt));
      const untimedLegacyDraft = stored && !stored.updatedAt && !savedAt;
      if ((storedIsNewer || untimedLegacyDraft) && stored?.schemaVersion === 3 && usableDrafts(stored.drafts)) {
        drafts = stored.drafts;
        actions = Array.isArray(stored.actions) ? stored.actions : [];
        saveStatus.textContent = 'Restored unsaved browser draft';
      } else if ((storedIsNewer || untimedLegacyDraft) && stored?.order?.length === payload.order.length && stored.order.every((id) => photoById.has(id))) {
        drafts = makeBaseDrafts(stored.order, Array.isArray(stored.exhibitionEmpty) ? stored.exhibitionEmpty : []);
        actions = Array.isArray(stored.actions) ? stored.actions : [];
        saveStatus.textContent = 'Restored unsaved browser draft';
      }
      nextBlockNumber = drafts.book.length + drafts.exhibition.length + 1;
      render();
      await loadHistory();
    } catch (error) {
      sequenceNode.innerHTML = `<p class="catalog-empty">${escapeText(error.message)}</p>`;
    }
  }

  start();
})();
