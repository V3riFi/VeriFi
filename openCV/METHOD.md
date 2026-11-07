# VeriFi method

## 1. The question

VeriFi answers one question: **does this person recognize these details?** It doesn't answer "is this person lying?"

That is the Concealed Information Test (CIT), the best-validated use of physiological and reaction-time measures in deception research. The investigator knows the facts (a real identity, the details of a crime), and the test asks whether *this person* knows them too.

- **Identity scenario (Security & Defense):** someone claims a false identity, and investigators have a candidate real one. Does the person recognize its first name, last name, hometown and mother's name?
- **Mock-crime scenario (validation):** a sealed envelope makes the subject guilty (memorize a stolen item, a code word and a hiding place) or innocent, at random. This gives a ground truth we control.

## 2. Why reaction time and physiology, not face "tells"

The first version scored face cues during a card test: blinks, lip compression, hand-to-face, gaze and head motion. It was **at chance**:
- **Card test:** in 6 recorded runs the real card ranked 3, 1, 2, 5, 1 and 4 of 5, and no p-value was below .2.
- **Known answers:** even a round where the system knew every answer singled out 1 of 5, exactly chance.

The reasons:
- **No validated link to recognition:** those cues have none. The CIT measures with real evidence are skin conductance, breathing suppression, heart-rate slowing, the P300, and **reaction time**.
- **Far too few trials:** 4 per item for a weak signal.
- **Talking:** saying "no" out loud moves the mouth, lips and blinks.

This matches the literature we read:
- **Speech timing:** in a deception study using a social-deduction board game, lies showed timing effects (pauses) but no arousal effects (pitch).
- **Video lie classifiers:** these are trained on instructed lies or courtroom video, and don't transfer to a CIT.

So in the final method **reaction time is the core signal, physiology is the second one, and the camera checks data quality** (section 6).

## 3. Test design

**Reaction-time CIT** (after Lukács et al., 2017, "enhanced" RT-CIT; meta-analysis d ≈ 1.3, Suchotzki et al., 2017):
- **Words:** one at a time. E = YES, I = NO, deadline 0.8 s, WRONG / TOO SLOW feedback, a blank of 0.25–0.75 s between words.
- **NO:** every candidate, including the true detail. Recognizing a word but having to answer NO causes a conflict, which slows the response.
- **YES:** 2 *target* words the subject memorizes per part, plus fillers (FAMILIAR / MINE / RECOGNIZED). The fillers force reading every word. They also make the YES key mean "I recognize this", which enlarges the slowdown on the true detail.
- **NO fillers:** UNFAMILIAR / OTHER / FOREIGN … keep the YES share at 20%.

| Choice | Why |
|---|---|
| 4 details: first name, last name, hometown, mother's first name | High-importance details known only to the person. Several details per test (multiple-probe protocol) add evidence. Birthday is optional (`--details`): dates are hard to tell apart in 0.8 s |
| 4 alternatives per detail, matched to the person (`interview/regions.py`) | The true word must not stand out by its look. **Names:** same style (South Asian, English/European, East Asian, Middle Eastern, Hispanic, African) and gender; mothers' names from the parent generation. **Towns:** same province/state, similar size, more than 60 km away. Unknown cases are asked once in the console, or the operator types the alternatives |
| Personally meaningful alternatives replaced | A friend's name among the alternatives would react too. The console asks before the test |
| Parts of 2 details | Only 2 target words to remember at a time. With 4 at once, target accuracy fell as low as 27% (memory load). The number of presentations per detail is unchanged |
| 14 presentations per word (`--quick`: 10) | Power. On our runs, 10 was the minimum that still detected the weakest responder (section 8) |
| Control run (`--control`) | A random identity of the same kind that the person never had. It measures false alarms live |
| Labels only in files | `TRUE`, `F1`–`F4`, `YES`, `BUFFER`. The words themselves are never saved |

**Sensor round** (before the keyboard round, because skin conductance habituates):
- **What's shown:** the same detail sets, one word every 6.8–7.8 s for 3 s.
- **Order:** each detail starts with an unscored buffer word, then 3 shuffled passes (2 with `--quick`).
- **Subject:** presses I (NO) with the right hand, keeps the sensor hand (left) still and doesn't talk.

**Full session** (`run_verifi.bat`):
1. Sensor round on the subject's own details (known answers: calibration).
2. Sensor round on the case.
3. Keyboard round on the case.
4. Keyboard round on their own details (calibrates the keyboard channel; skipped with `--quick`).

## 4. Scoring

**One scorer for every channel** (`interview/scoring.py`):
1. Each presentation round (every word once) is one *series*.
2. Within a series, each cue is z-scored against the other words, so slow drift and anything common to all words cancel out.
3. The signed, weighted cues are summed per presentation, then averaged per word.
4. A permutation test shuffles the labels within each series 4000 times. It gives a one-sided p for the known true word, valid under the null by construction.

**Keyboard channel:**
- **Cue:** log reaction time of correct answers.
- **Several details:** they are scored together in one test.
- **Exclusion rules** (literature): candidate accuracy < 75% → INVALID.
- **One-sided YES rule:** fewer than 50% hits on the memorized target words only blocks "no evidence" (it becomes "inconclusive"). It never blocks "recognizes": missing YES words can hide a recognition effect but can't create one.

**Sensor channel**, per presentation:

| Cue | Window | Direction for a recognized detail | Prior weight |
|---|---|---|---|
| `scr_amp`: skin-conductance peak minus baseline | peak 1–5 s vs. mean −1–0 s | ↑ | 1.0 |
| `resp_length`: breathing line length per second | 0–6 s | ↓ (shallower) | 0.75 |
| `hr_change`: heart rate after minus before | 0–5 s vs. −1–0 s | ↓ (brief slowing) | 0.5 |
| `kp_log_rt`: the key press for that word | the press | ↑ | 0.75 |

All cues are measured on the *same* presentations, so they are combined per presentation and then tested once. Multiplying separate p-values from the same trials would overstate the evidence.

**Combining channels:** the keyboard and sensor rounds are independent, so their p-values combine with a weighted Stouffer Z. Under the null this keeps 5% false alarms at p < .05 (tested). A missing or invalid channel is left out.

**Subject Response Profile** (`interview/profile.py`, full session). The calibration round has known answers, and it decides how much each cue counts for this person:
- **Sensor cues:** weight = prior × a factor from the calibration z, shrunk toward the prior and kept within × 0.25–2.
- **Channels:** each channel's weight = 1 + 0.25 × its calibration z, clipped to 0.5–1.5.
- **Face cues** start at weight 0. One counts only if it separated the known details by z ≥ 1, with weight 0.25 · min(z, 2).

Everything is fixed before the test round is scored, so the test p stays valid.

**Verdict** (`interview/verdict.py`, thresholds fixed before any data):

| p | Verdict |
|---|---|
| < .05 | **RECOGNIZES these details** |
| .05 – .20 | **INCONCLUSIVE**: a slight lean, but about 25% of innocents land here |
| ≥ .20 | **NO EVIDENCE** (or INCONCLUSIVE if too many YES words were missed) |
| task not followed | INVALID |
| stopped early | INCOMPLETE: partial scores are shown, but it's not a result and isn't counted |

## 5. Sensors

`sensors/` reads the Arduino over USB serial, or samples the backend forwards over UDP. It timestamps every sample **on arrival with the interview laptop's clock**, the same clock as the word onsets.
- **Before each session:** `run_sensor_check.bat` checks rate, flat signals and GSR polarity (`--gsr-inverted` for boards that read resistance).
- **Simulated data** (`--sensor sim`) is marked in the file and never enters a score.

Formats and wiring: [HARDWARE.md](HARDWARE.md).

## 6. The camera: quality control

The camera decides whether a trial is **usable**, not whether someone is lying (`vision/offline.py`).
- **Keyboard trials:** dropped when the face is missing, the eyes are closed (eye openness < 50% in ≥ 60% of the frames) or the person looks away (head turned > 20° or tilted > 15° from their own median, or gaze off-axis) in the first 150 ms of the word.
- **Sensor-round words:** dropped for head motion, talking, a hand on the face or a posture shift in the −1 to +6 s window. Each of these moves skin conductance or breathing by itself.
- **Thresholds:** absolute floors plus robust per-person outliers (median + 3.5 MAD).
- **Label-blind:** the rules never look at which word was shown. Dropping can lower sensitivity but can't create a detection.
- **Safety cap:** if the face is visible < 70% of the time, or the check would drop > 25% of a round, it isn't applied and the report says to fix the camera.
- **Countermeasure check:** if artifacts pile up on the tested details (hypergeometric p < .01), the report says so.

This check also exposed a blink-detector bug, now fixed and tested. When eye openness settled between the closing and reopening thresholds (looking down at the keys), the eye stayed "closed" for up to 20 s.

## 7. Validation plan

**Variants** (pre-registered). Each adds one idea; `full` is the headline:

| Variant | Adds |
|---|---|
| `rt_only` | the keyboard round alone |
| `rt_plus_sensors` | + the sensor round |
| `plus_gating` | + the camera checks |
| `plus_profile` | + the per-person weights |
| `full` | + face cues that earned a weight |

`examples/evaluate.py` reports, per variant, the detection rate (knowers at p < .05), the false-alarm rate (non-knowers at p < .05) and the AUC. A variant that doesn't beat the one before it gets dropped. That is how the sensors and the camera have to prove themselves.

**Targets:**
- **Mock crime:** at least 10 guilty and 10 innocent sessions.
- **Identity:** at least 10 people, each run with real details and with `--control`, in random order.

**Expected accuracy (simulation).** Assumptions:
- an autobiographical-item effect of 28 ms on average (SD 15 ms between people)
- trial SD 80 ms
- 40 runs per row

| Design | Words (time) | Real details detected (p < .05) | Control false alarms (p < .05) |
|---|---|---|---|
| 4 details × 10 (`--quick`) | 300 (~6 min) | 50% | 8% |
| **4 details × 14 (default)** | 420 (~8.5 min) | **62%** | **2%** |
| 4 details × 18 | 540 (~11 min) | 68% | 10% |

These are from published effect sizes, not our data. The real numbers come from the validation above.

## 8. Results so far

Identity test, real details unless marked. The keyboard round only (no sensors yet):

| Run | Setup | Slowing on true details | p | Verdict |
|---|---|---|---|---|
| 1 | 4 details at once | +48 ms | .001 | RECOGNIZES |
| 2 | 4 at once; alternatives didn't match the person's background (since fixed) | +74 ms | < .001 | RECOGNIZES (weaker evidence: the true words could stand out by look) |
| 3 | parts of 2 | +76 ms | < .001 | RECOGNIZES |
| 4 | parts of 2; camera dropped 69 of 420 trials (looking away, face lost) | −5 ms | .36 | NO EVIDENCE (a miss) |
| 5 | stopped after 75 of 420 words | — | — | INCOMPLETE, not counted |
| control | random identity, 4 at once | −8 ms | .75 | not flagged (INCONCLUSIVE: too few YES hits to rule out) |

That's 3 of 4 detected and 0 of 1 false alarms. That is a pilot, not an accuracy claim.

**How many presentations are needed?** We re-scored each run using only its first N rounds:
- **Run 1:** needs 10 rounds (p = .033 at 10, .13 at 8).
- **Run 3:** detected from 6.
- **Run 4:** not detected at any length.
- **Control:** stays above .4 at every length.

Hence 14 rounds by default and 10 as the `--quick` minimum.

Physiology and the full mock-crime session have been tested end to end on simulated data only. The effect size with our sensors is still unknown.

## 9. Limits

- **A reaction means recognition, not lying.** A familiar alternative or a personal connection can trigger one. Meaningful alternatives are replaced before the test for this reason.
- **Countermeasures:** deliberately slowing down on chosen words can defeat the reaction-time channel. Physiology and the camera's countermeasure check make this harder, not impossible.
- **Small samples, weekend prototype.** Not for real investigations, hiring, or any decision about a person.

## References

- Lukács, G., Kleinberg, B., & Verschuere, B. (2017). Familiarity-related fillers improve the validity of reaction time-based memory detection. *Journal of Applied Research in Memory and Cognition*.
- Suchotzki, K., Verschuere, B., Van Bockstaele, B., Ben-Shakhar, G., & Crombez, G. (2017). Lying takes time: A meta-analysis on reaction time measures of deception. *Psychological Bulletin*.
- Meijer, E. H., Verschuere, B., Gamer, M., Merckelbach, H., & Ben-Shakhar, G. (2016). Deception detection with behavioral, autonomic, and neural measures: Conceptual and methodological considerations that warrant modesty. *Psychophysiology*.
- Lukács, G., & Ansorge, U.: RT-CIT with the subject's own country of origin as the hidden item (PubMed Central PMC8440312).
