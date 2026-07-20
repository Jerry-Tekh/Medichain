(function initFormSubmission(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.MediChainFormSubmission = api;
}(typeof window !== "undefined" ? window : globalThis, function formSubmissionFactory() {
  const states = new WeakMap();

  function isBusy(form) {
    return Boolean(states.get(form)?.busy);
  }

  async function optionalRecord(callApi, path) {
    try {
      return await callApi(path, { method: "GET" });
    } catch (error) {
      if (error && error.status === 404) return null;
      throw error;
    }
  }

  async function guardedMutation({
    callApi,
    duplicatePath,
    requiredPath = "",
    mutate,
  }) {
    const existing = await optionalRecord(callApi, duplicatePath);
    if (existing !== null) return { kind: "duplicate", record: existing };

    if (requiredPath) {
      const required = await optionalRecord(callApi, requiredPath);
      if (required === null) return { kind: "missing" };
    }

    return { kind: "submitted", value: await mutate() };
  }

  async function run({
    form,
    pendingLabel,
    onBusyChange = () => {},
    onSettled = () => {},
    task,
  }) {
    if (isBusy(form)) return false;

    const submitButton = form.querySelector('button[type="submit"]');
    if (!submitButton) throw new Error("Form has no submit button");
    const state = {
      busy: true,
      originalDisabled: submitButton.disabled,
      originalLabel: submitButton.textContent,
    };
    states.set(form, state);
    form.setAttribute("aria-busy", "true");
    submitButton.disabled = true;
    submitButton.textContent = pendingLabel;
    onBusyChange(true, pendingLabel);

    try {
      await task();
    } finally {
      state.busy = false;
      form.setAttribute("aria-busy", "false");
      submitButton.textContent = state.originalLabel;
      submitButton.disabled = state.originalDisabled;
      onBusyChange(false, pendingLabel);
      onSettled();
    }
    return true;
  }

  return {
    guardedMutation,
    isBusy,
    optionalRecord,
    run,
  };
}));
