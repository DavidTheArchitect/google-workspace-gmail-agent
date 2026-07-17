# Standard and advanced Gmail blocking

The agent has two separate blocking surfaces because Google Workspace exposes two different
administrative features.

## Standard blocked senders

The standard editor manages exact email addresses and entire domains through address lists under
**Apps > Google Workspace > Gmail > Spam, Phishing and Malware > Blocked senders**. It supports:

- create, list, update, enable/disable, and remove for application-owned rules;
- exact root or child organizational-unit paths;
- separate blocked and approved-sender bypass address lists;
- a custom sender-facing rejection notice; and
- independent list and rule ownership records, so a bypass list cannot be mistaken for a block
  list.

Google notes that these blocks automatically reject matching messages, can use address lists, can
exempt approved senders, and can return a custom notice. See Google's
[blocked-senders documentation](https://support.google.com/a/answer/2364632?hl=en).

## Content compliance as an advanced blocker

The advanced editor manages **Reject message** rules under
**Apps > Google Workspace > Gmail > Compliance > Content compliance**. It supports all four Google
message directions:

- inbound;
- outbound;
- internal sending; and
- internal receiving.

Each rule combines one to ten typed expressions using **any** or **all**:

- simple content;
- advanced content at headers/body, full headers, body, subject, sender header, recipient header,
  envelope sender, envelope recipient, or raw message;
- metadata such as authentication, source IP, secure transport, S/MIME, size, confidential mode,
  and security-sandbox malware; and
- predefined content detectors when the current Workspace edition exposes the required capability.

Advanced regular expressions compile with the `google-re2` runtime before preview. The application
enforces Google's 10,000-character ceiling and never translates a PCRE-only construct into a
different pattern.

Google documents simple, advanced, metadata, and edition-dependent predefined matching. For a
matching rule, **Reject message** rejects before recipient delivery, sends the configured rejection
text to the sender, and adds the required SMTP rejection code automatically. See Google's
[advanced content-filtering documentation](https://support.google.com/a/answer/1346934?hl=en).

The agent intentionally does not authorize quarantine, modification, routing, or a private API.
Those capabilities exist in Google Admin, but this project is blocking-focused and keeps its action
union closed to Reject.

## Dynamic rejection personas

The local model creates a fresh fictional role, traits, voice, motif, and bounce message at request
time. Every attempt receives a new cryptographic sampler seed and entropy nonce, with no stock
character bank, canned creative launch vector, example persona, or application-level tone filter.
The console keeps a short session history and resamples exact or near-duplicate profiles instead of
showing them twice in succession. Invalid structured output is retried with fresh entropy; if the
bounded attempts fail, the previous draft stays visible and the console reports the failure rather
than substituting canned copy.

Every draft also passes a deterministic sender-safety quality gate before it reaches the operator.
Drafts that leak escape-sequence artifacts, markup or structured-data characters, non-printable
characters, fabricated email addresses, web addresses, domain names, or phone-number-like sequences,
expose the internal policy-category label, or return a sentence-like role instead of a compact title
fail the attempt and are resampled with fresh entropy. Line endings and stray whitespace are
normalized without rewriting the model's prose. The bounce-message category itself is
application-owned identity, is never included in the creative prompt, and is not editable from the
rejection-notice editor.

The application still owns policy identity and disclosure boundaries. The sender-facing text may
explain the refusal in generic policy terms but never disclose the policy category, policy ID,
triggering header, regular expression, address, domain, metadata value, security signal, credential,
or another internal identifier. Those are data-protection invariants, not creative-content
moderation. The local model may retain behavior learned during its own training that this
application cannot disable.

## Multi-agent and browser execution

Four local participants run in the Microsoft Agent Framework group-chat pattern:

1. policy architect;
2. RE2 and expression reviewer;
3. ownership, blast-radius, and safety reviewer; and
4. operator advocate.

Their discussion is advisory. A final zero-temperature planner must still produce a valid
`TaskPlan` 2.0 object.

For UI execution, Gemma receives a screenshot, a bounded accessibility snapshot, and a catalog of
visible controls. It returns one typed step referencing an opaque candidate ID and, when needed, an
application-owned input token. The executor independently requires:

- `https://admin.google.com` as the current host;
- a known Gmail compliance page state;
- one unique, visible, enabled semantic control;
- a still-valid hash-bound approval;
- the approved organizational unit visible before a commit control; and
- a bounded step count followed by a fresh editor read-back.

Browser credentials, cookies, and the persistent profile remain outside prompts and audit data.
`CA_BROWSER_MODEL` selects the locally installed vision-capable Ollama model independently from the
planning model. It defaults to the requested Gemma model; deployments whose Gemma build does not
accept images can select another local vision model without changing the planner or safety gates.

Create, update, enable/disable, and remove are distinct approval operations. A browser permit binds
one operation, one managed rule identity, one OU, and the three preview hashes, and is consumed on
first use. The writer supplies typed tokens for expression type, content location, operator, value,
metadata attribute, predefined detector, address-list conditions, and envelope filters, then checks
those same visible fields during independent read-back.

## Current-UI acceptance

Google changes the Admin console markup independently of this project. A live deployment must run
the supervised fixture and selector acceptance procedure in
[live-test-procedure.md](live-test-procedure.md) for its current tenant before production use. The
attended vision driver is built in and discovers current semantic controls, but a disposable-rule
acceptance run remains the strongest evidence that the tenant edition and present Google UI expose
the expected fields. This is not a recommendation against Content compliance.
