const assert = require("node:assert/strict");
const {
  guardedMutation,
  run,
} = require("./form_submission.js");


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function fakeForm(label = "Submit", disabled = false) {
  const button = { disabled, textContent: label };
  return {
    attributes: {},
    button,
    querySelector: () => button,
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}

async function testRapidRepeatRunsOneMutation() {
  const form = fakeForm("Register Trial");
  const pending = deferred();
  let mutations = 0;
  const options = {
    form,
    pendingLabel: "Registering...",
    task: async () => {
      mutations += 1;
      await pending.promise;
    },
  };

  const first = run(options);
  const second = await run(options);
  assert.equal(second, false);
  assert.equal(mutations, 1);
  assert.equal(form.button.disabled, true);
  assert.equal(form.button.textContent, "Registering...");
  assert.equal(form.attributes["aria-busy"], "true");

  pending.resolve();
  await first;
  assert.equal(form.button.disabled, false);
  assert.equal(form.button.textContent, "Register Trial");
  assert.equal(form.attributes["aria-busy"], "false");
}

async function testStateRestoresAfterErrorAndDisconnect() {
  const form = fakeForm("Submit Results for Analysis");
  let permitted = true;
  const pending = deferred();
  const request = run({
    form,
    pendingLabel: "Submitting for analysis...",
    task: () => pending.promise,
    onSettled: () => {
      form.button.disabled = !permitted;
    },
  });

  permitted = false;
  pending.reject(new Error("request failed"));
  await assert.rejects(request, /request failed/);
  assert.equal(form.button.disabled, true);
  assert.equal(form.button.textContent, "Submit Results for Analysis");
  assert.equal(form.attributes["aria-busy"], "false");
}

async function testDuplicatePreflightSkipsPaidMutation() {
  let mutations = 0;
  const result = await guardedMutation({
    callApi: async () => ({ trial_id: "TRIAL-001", status: "active" }),
    duplicatePath: "/api/trial/TRIAL-001",
    mutate: async () => {
      mutations += 1;
    },
  });
  assert.equal(result.kind, "duplicate");
  assert.equal(mutations, 0);
}

async function testDuplicateReportPreflightSkipsTrialReadAndMutation() {
  let mutations = 0;
  const paths = [];
  const result = await guardedMutation({
    callApi: async (path) => {
      paths.push(path);
      return { report_id: "REPORT-001", verdict: "clean" };
    },
    duplicatePath: "/api/report/REPORT-001",
    requiredPath: "/api/trial/TRIAL-001",
    mutate: async () => {
      mutations += 1;
    },
  });
  assert.equal(result.kind, "duplicate");
  assert.deepEqual(paths, ["/api/report/REPORT-001"]);
  assert.equal(mutations, 0);
}

async function testReportPreflightRequiresExistingTrial() {
  let mutations = 0;
  const paths = [];
  const result = await guardedMutation({
    callApi: async (path) => {
      paths.push(path);
      const error = new Error("not found");
      error.status = 404;
      throw error;
    },
    duplicatePath: "/api/report/REPORT-001",
    requiredPath: "/api/trial/TRIAL-001",
    mutate: async () => {
      mutations += 1;
    },
  });
  assert.equal(result.kind, "missing");
  assert.deepEqual(paths, [
    "/api/report/REPORT-001",
    "/api/trial/TRIAL-001",
  ]);
  assert.equal(mutations, 0);
}

async function testPreflightDoesNotSwallowReadFailures() {
  await assert.rejects(
    guardedMutation({
      callApi: async () => {
        const error = new Error("Bradbury unavailable");
        error.status = 502;
        throw error;
      },
      duplicatePath: "/api/trial/TRIAL-001",
      mutate: async () => {},
    }),
    /Bradbury unavailable/,
  );
}

(async () => {
  await testRapidRepeatRunsOneMutation();
  await testStateRestoresAfterErrorAndDisconnect();
  await testDuplicatePreflightSkipsPaidMutation();
  await testDuplicateReportPreflightSkipsTrialReadAndMutation();
  await testReportPreflightRequiresExistingTrial();
  await testPreflightDoesNotSwallowReadFailures();
  console.log("6 form submission tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
