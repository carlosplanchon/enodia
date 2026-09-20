# Why Enodia is deliberately conservative

Enodia answers a question nobody can check afterwards. You cannot go back and find out where
you really were at 17:52:10, so every answer it gives is one you have to take on trust, and the
only defence against that is a program that says what it does not know. Most of the design here
is that one rule applied in different places, and most of it was learned by getting it wrong
first. This file is the record of that: what the program refuses to do, and the mistake that
taught it to refuse.

The [README](../README.md) says what Enodia does. This says why it is like this.

## Not knowing is an answer

Four rules do most of the work. **Not knowing is an answer**: off the map every fingerprint is a poor match and the best of them is still wrong, so below a floor it says "not on the map" rather than guessing confidently in a city it has never been to. The floor is applied to each fingerprint before any of them votes, which matters more than it sounds: a stretch wins on its fingerprints added up, so four readings too weak to be evidence used to outvote the one reading that cleared the floor, and the answer came back naming the losers' street. Nothing that is not evidence on its own becomes evidence by turning up four times. **Two streets are never averaged**: when two stretches match about as well, both are reported, because the midpoint of two streets is inside the block between them, where you certainly were not. **A stretch is one stretch whichever way you walked it**: the two crossing names are sorted before anything is stored, and compared with case, accents and spacing folded away, so `Yaguarón` one week and `Yaguaron` the next do not quietly become two streets that never match. And **a crossing name means one place**: matching is done on the networks in view, which know nothing about geography, so a map holding two towns that both have an "Artigas y Rivera" can win with fingerprints 200 km apart, and the weighted middle of them is a field between two cities. When the fingerprints behind the winner are further apart than three of that stretch's own lengths (five kilometres, for a stretch whose length nobody knows), the stretch is still named and the coordinates are withheld, with a line saying so. Crossing names have to be unique across a map, and no map fed one outing at a time can enforce that for you.

## A failure to read is not evidence of absence

The rule that comes up most often, because it has the most ways to go wrong. A file that cannot
be opened, a sysfs read that fails, a daemon that will not answer: none of those is the same as
a file with nothing in it, and reporting them as an empty answer produces a confident, wrong
result that looks exactly like a good one.

A configuration file that is there and cannot be read is the same kind of answer. The daemons read those files with their own privileges and Enodia does not, so a drop-in in `/etc` that it cannot open is not a file that says nothing: it is a file that may say the opposite of everything else. Both the lid and the scan MAC lines say plainly that they could not establish the configuration in force, rather than reporting what the readable half of it said.

The same argument is why `--streets` raises on a file it cannot read rather than falling back to
a map with no streets in it. An empty map puts every block back on the straight line between its
crossings, which is a quietly worse answer wearing the same face as a good one. A file that is
not there yet is the exception, because that is the ordinary case before `--geocode` has written
it.

And the map file has the strictest version. `read_map` treats only a missing file as an empty
map, because everything else it could fail on is a map whose contents are unknown, and locating
yourself against an unknown map is worse than saying so.

## Refused at the door

Nothing in the file is taken on trust when it is read back. It is a text file that gets copied about, cut in half and opened in an editor, and a value of the wrong type used to travel a long way before anything noticed. A `cycle` of `"oops"` stopped the next outing from starting at all, because the monitor reads the highest cycle in the file before its first scan and then compared a string to a number. So every value is checked at the door, and a field that fails is dropped rather than carried. A measurement keeps its fraction, since a radio reporting -47.5 dBm has measured something. A `cycle` or a mark `number` is an identity and not a measurement, so a fraction there is refused instead of rounded: cycle 1.7 quietly becoming cycle 1 would fold a look at one place into a look at another. And a name that is not a string is not a name, which is how an SSID stopped arriving in the report printed as `['x']`. A timestamp has to carry its offset from UTC, which every timestamp Enodia writes does: Python refuses to compare a naive datetime with an aware one, so one edited line without an offset did not cost itself, it took down every scan in the file out of the sort that puts them in order. Being a number is not enough on its own either, so a reading also has to be one a receiver could have taken. A signal of 100000 dBm reached the weighting, which raises ten to it, and came back as an arithmetic error from inside an estimate. A plausible-looking 500 never raises anything and simply wins every weighting it is in, which is the worse of the two. Both the file and the live scan go through the same door.

Because it is a file that gets copied about and edited by hand, every number in it is checked on the way back in rather than trusted: a latitude has to be a number and to be finite and to fall between -90 and 90, a fraction between 0 and 1, and half a coordinate is no coordinate. A BSSID comes in as one spelling of itself, since hexadecimal written out has two of every letter and that string is what one network being another is decided by: `AA:BB:CC:DD:EE:FF` in a hand-edited map and `aa:bb:cc:dd:ee:ff` in a fresh scan are one access point, and they used to score nothing in common. The SSID keeps its case, because `Casa` and `casa` are two names somebody chose. JSON will carry `NaN`, and Python reads it without complaint, and one of them poisons every average it reaches, so it is refused at the door where it costs one line instead of turning up later as an answer nobody can explain. The same goes for the log.

A line that cannot be right stops the reconciliation with its number and its reason rather than being read as best it can. The 31st of February is not a date, a latitude of 999 is not a place on the earth, `17:0 A` is a time typed wrong rather than a crossing named `17:0 A`, and `date 2026-9-18` is a directive typed wrong rather than a crossing named after it. That last one is worth spelling out: a line with no time is a crossing that takes the next button mark, and almost any line can be a crossing's name, so without a rule for it a typo stopped being an error and quietly became a notebook of a different kind. That second one matters more than it looks: the notebook is the ground truth every other check is measured against, so an impossible coordinate fails nowhere, it produces distances and geometry that are absurd and that look exactly as legitimate as the rest of the report. And somebody who typed `@` meant to give a coordinate, so reading the line as a crossing without one would quietly take the whole notebook down to the answers it can give with no coordinates anywhere.

## One record, and two ways of writing it down

A log is written by `record_to_json` and read by `_record_from_json`, and those two are a pair
on purpose. Between them sits `LogRecord`, which is what a record actually is in this program:
the JSON is one way of writing it down and not the thing itself. `SeenNetwork` is the same
arrangement one level below, with `seen_network` as the radio's door into it and
`network_from_json` as the file's, so a network heard a second ago and a network read back off
the disk are one kind of thing rather than two that resemble each other.

That sounds like tidiness and it is not. `--export-public` was written as a transformation of
files: open the JSONL, interpret it again, decide what was valid, substitute, serialise again.
That is a second reader of the same format, and every bug found in the export over three rounds
came out of the two readers disagreeing. The first round it was networks: `"bssid": ["bad"]`
took the export down with an AttributeError, and a `NaN` went out into a file Enodia's own
reader then discarded. Routing networks through `network_from_json` fixed those. The records
themselves were still being copied field by field out of the raw dictionary, so the same two
failures were still there one level up: a `networks` that was not a list took the export down,
and a `cycle` of `"abc"` was published as `"abc"` and read back as nothing, out of the one
command whose whole job is knowing exactly what it published.

So the export serialises records rather than rewriting files. It reads the log the way
everything else reads it, picks its walk with `records_for_outing`, which is the one place that
knows a record naming no token belongs to the walk in force where it sits, and writes each one
out by naming fields off the typed record. That last part is what turns the rule round. The old
whitelist said which keys of an arbitrary dictionary were allowed to leave, so the default for
anything new was to be copied and the safety came from remembering to think about it. Naming
fields off a type inverts it: a field added to `LogRecord` next year does not appear in an
export until somebody writes it in. The raw JSON is no longer something the anonymiser has to
trust, because the anonymiser no longer reads it.

Two things follow that are worth saying out loud. A field the writer writes and the reader
drops is a field that breaks the pair, and `key`, the code of the button a mark was pressed
with, was exactly that: it is in `LogRecord` now, because the export having to reach behind the
type to find it is how the second reader got justified in the first place. And a network that
hides its name is written `""` rather than `null`, since that is what the reader hands back,
and the file may as well say what it is going to mean.

The public log is not written during the walk, which was the other way to close the same gap.
It cannot be: the exported clock and the exported geometry are both laid out from the first
moment and the first coordinate of the log **and the notebook together**, and the notebook does
not exist until afterwards. A file written live would have a different epoch and a different
origin from the one the export produces, which is two public versions of one walk that disagree.
It would also mean the export key sitting in a process that runs for four hours in a bag. The
public copy is a function of the private log, the notebook and the key, and a thing that can be
derived is not a thing to store.

## Nothing else is allowed to end the walk

Nothing else is allowed to end the walk. Reading which network a card is associated to is a question for the daemon, and reading its rfkill switch is a file in `/sys`, and either can fail on its own for reasons that say nothing about whether the radio works. A walk is four hours in a bag where the only recovery is getting home. So those reads are wrapped: the failure is said out loud and the cycle carries on scanning, and it is not written down as a failed scan, because the scan did not fail. `--preflight` is careful in the same direction, and says `switch unreadable` for a card it could not ask, which is not the same answer as a card that said it was blocked.

That is the other half of the same argument. Refusing bad input at the door is worth doing
because the alternative is a wrong answer. Refusing to carry on when a peripheral read fails
is not, because the alternative is four hours in a bag with nothing recorded. Which of the two
applies depends on whether the failure has anything to do with the answer.

## Identity, and how the clock stopped being enough

Three times over, the same lesson one size up. Something that was identified by when it
happened turned out to need an identity of its own, because the clock has one second of
resolution and things that are genuinely different can share a second.

Every record carries the `outing` it belongs to, a token for one run of the loop, and a scan record also carries the `cycle`, the pass of the loop it came from. Watching two interfaces writes a record each, and they are one look at one place from one pair of feet, so a reconciliation puts them back together before it reads the pace off them. The timestamp cannot say this: it has one second of resolution, and a cycle whose scans land either side of a second would come apart. The number is unique inside the file rather than inside the run, so a restart carries on from the highest one already written instead of beginning again at one and folding two places into one. The outing token answers the same question one size up: one file can hold several walks, the clock cannot tell apart two that began in the same second, and `--resume` adopts the token already in the file because that is the same walk continuing rather than a second one sharing the pages. It goes on every record and not only on the scans, because the button marks need it just as much. Every run numbers its marks from one, so a file of two walks holds two mark 1s, and read as one file the second walk's took the place of the first walk's: a notebook of `#1` and `#2` came back with the times of a walk on another day. A scan record is always what the radio heard: a cycle without a trustworthy scan is a `scan_failed` record with its `reason` (`radio soft blocked (rfkill)` among them), never an empty scan. Records that belong to one interface carry `interface`, which is what keeps scans apart when more than one is watched. An association is recorded with its BSSID, so two `connected` records with the same name and different BSSIDs are a roam between two access points of one network, not a reconnection. It carries the signal in the same fields a scanned network uses, which is what makes a roam readable afterwards: you moved because the first one was fading. A network is counted once per cycle and never once per instant, for the same reason: two cycles fit inside one second of the clock, and each is a real sighting from a real place.

An outing is named by when it began and by the token the walk wrote on its own scans, never by the log's filename, since `--log walk.jsonl` reused every week appends to the same pages. Nor by the clock alone, which is what this was: the timestamp has one second of resolution and two walks can begin inside one, and then the map refused the second as already added and `--check-map` held the two out together as one. It is the lesson `cycle` taught one layer down. A log written before the token existed still falls back to the clock, the same way a log written before `cycle` falls back to it for grouping. A `walk` is one outing down one stretch, which is the unit `--check-map` holds out. `from`, `to` and `fraction` are in the sorted frame, while the names stay as the notebook wrote them. Only what matching uses is kept: the security and the band are not, since neither says anything about where you are.

A network with nothing to identify it is kept in the log and left out of all of this. A network is known by its BSSID, or by its name when the backend gives no BSSID, which iwd sometimes does not, and a network that also hides its name leaves neither. Two of those are not the same network, they are two networks nothing can tell apart, and treated as one they agreed with each other completely: a fresh scan of one anonymous router scored a perfect match against a remembered, different one, and the map answered with a place. So they are never folded together, never matched, never counted as turnover and never placed. A coincidence of absence is not evidence.

A BSSID is compared in one spelling of itself for the same reason. Hexadecimal written out has
two of every letter, and that string is what one network being another is decided by, so
`AA:BB:CC:DD:EE:FF` in a hand-edited map and `aa:bb:cc:dd:ee:ff` in a fresh scan used to score
nothing in common. The SSID keeps its case, because `Casa` and `casa` are two names somebody
chose rather than two spellings of one.

A notebook is one walk's, so a reconciliation is one walk's too. The usual file holds exactly one and there is nothing to choose. `--log walk.jsonl` reused every week is the other case, and there Enodia reads the last walk in the file and says which one that was, with `--outing TOKEN` to name an older one. Everything that reads a log works this way: `--map-add`, `--locate LOG`, and `--geocode --marks LOG` too. Reading the whole file instead was wrong in two ways that both looked like answers. The day of a notebook without a `date` line came from the first scan in the file, so a notebook of the second walk was read against the first walk's date and every scan of it fell outside the notebook. And every run numbers its button marks from one, so the second walk's mark 1 took the place of the first walk's, and a notebook of `#1` and `#2` was given the times of a walk on another day. `--resume` counts on from this walk's marks for the same reason: an older walk in the same file that reached mark 100 would otherwise have the next press announced as 101, while the paper in your hand says 3.

## The button, and which clock a press happened on

The notebook's weak point is the time: heard through headphones, written by hand, one misheard digit moves a whole stretch of the route. A headset has a button, and Linux shows it as an input device that reports every press with the time the kernel stamped it with, which is when the press happened rather than when anything got round to reading it. Press it at each crossing and the time is the machine's. That stamp is what separates two presses: a thumb that bounces is one crossing, and two real presses that arrive together after a busy moment are two. Timing them by the reading instead made the second of those disappear. It is also what the `mark` record is dated by, back from the moment it was handled, so two crossings two seconds apart are two seconds apart on the paper as well. Which clock evdev stamped it with is determined rather than assumed, by asking each clock the machine has what time it is and seeing which one puts the press in the recent past: they cannot both, since one counts from 1970 and the other from boot. That is what lets a press which sat in the buffer while the laptop was busy still be written when it happened. When no clock recognises the stamp, the last press of a batch is read as happening now, which is what every press was read as before any of this. Enodia answers *"Mark 7"*, and the notebook only needs the crossing's name next to that number. The `mark` records in the log carry the times, and the reconciliation joins the two by number. One press is one crossing, so a notebook that writes `#1` twice is refused, and so is a notebook whose numbers the log cannot answer: that usually means the wrong walk of the log was picked, which is what `--outing` is for. A notebook with no numbers and no log is the ordinary other case, and it says the times are simply not known.

## What the machine says about itself

Both Wi-Fi daemons can randomise the address used for scanning, which is a different setting from the one used once you are associated, and NetworkManager does it by default. `--preflight` reads what yours was told and says so on the `scan mac` line, and warns when somebody has turned it off. It says `OK` for one thing only: NetworkManager, set once, in a section NetworkManager reads device properties from, to a value NetworkManager documents, with no mask. A value that is neither a yes nor a no is a warning about a value the daemon was not asked for, since reading anything that is not an off as an on turned a typo into a green line. A file held back by `[.config] enable=false` is skipped the way NetworkManager skips it, and one held behind a version or environment predicate is reported as a file whose effect is not established. The same key under `[main]` parses perfectly and does nothing, so it is reported as doing nothing rather than as the machine's setting. A `wifi.scan-generate-mac-address-mask` fixes some bits of the scanning address and randomises only the rest, so with one set the word is withheld: how much of the address actually varies is not worked out here. And iwd's own setting is never an `OK`, because it is documented as the address the interface uses, which is a related question and not this one.

Enodia does not change it, on purpose: that needs root, which nothing else here does, and it fights the daemon that owns the interface and will be reverted. The daemons already have a supported way that survives reconnects and suspends, which matters when the laptop spends four hours in a bag. So this catches rather than corrects, the way the lid and the corner names do.

## Suggesting a next step without pretending to know one

`--assistant` opens a menu that suggests what to do next, and the whole of that suggestion is a
short list of rules over files that exist. Not because a model would be worse at it, but
because the rules can be read, argued with and tested, and because the honest answer to most of
them is one this program can actually support.

| The state | What it suggests |
|---|---|
| No usable log anywhere | Walk a couple of blocks |
| The newest outing has button marks and no notebook beside it | Transcribe the notebook |
| An outing is not on the map, and something beside it reads as a notebook | Add it to the map |
| The map holds exactly one outing | Walk the same blocks again on another day |
| The map holds two or more | Measure it |
| None of the above | Nothing. The menu is drawn with no suggestion on it |

The last row is the one that matters. A menu that always has something to suggest will
eventually suggest something it made up, and this one is allowed to say nothing.

Three questions it is asked all the time and refuses to answer, because Enodia does not record
what they would need:

- **"Has this outing been reconciled?"** Nothing writes that down. A report goes to the terminal
  and leaves no trace, and even `--csv` leaves a file with no outing named in it. The knowable
  neighbour is **whether the outing is on the map**, which is cheap: a map's outing name is
  `<when it began>/<token>`, so the token after the slash answers it without reconciling
  anything. That is the question the rules actually ask, and the outings screen says in so many
  words that the other one is not shown because it is not written down.
- **"Is this notebook the one for this log?"** Nothing links them. There is no naming
  convention, and the assistant does not invent one: it offers the files that read as notebooks,
  says they are only what look like one, and a path can always be typed instead.
- **"Is the map any good?"** Only `--check-map` says anything, and only with two walks in it.
  With one, the screen says there is nothing to hold out and nothing to measure, and suggests
  the walk that would change that. And a map that is there and will not open is not a map
  with nothing in it: every rule below the notebook one turns on what the map holds, so when
  it cannot be read they are all suspended and the menu is drawn with no suggestion on it.
  A count worked out from a file nobody opened would be a number invented to fill a line.

## Adding to the map is a read, a change and a write

Which means two of them at once is a window. Both runs read the map as it was, both wrote their
own outing onto that, and whichever finished second left the first one's walk nowhere: no
error, no warning, a map quietly missing an outing somebody actually walked. It takes two
writers arriving at the same instant to see it, which is to say it takes a bad afternoon rather
than a malicious one.

So the three steps happen with the map held. The lock is a file beside it rather than the map
itself, because the map is replaced and not written in place, and a lock on the old file
protects nothing once the name points at a new one. Holding it across all three closes the
other half of the same window too, where both runs find the outing absent and both add it, and
a doubled outing pulls every answer towards itself.

The log has no equivalent problem. It is appended to and never replaced, and two walks writing
into one file is a thing the `outing` token already sorts out afterwards.

## Publishing a walk without publishing a neighbourhood

The README has asked for a long time for one real outing in the repository, and doing it by
hand means publishing the Wi-Fi landscape of somebody's street. `--export-public` is the layer
that makes it possible, and most of its design is about not overstating what it does.

**It is pseudonymization.** The command prints that word every time it runs, along with the
three things it cannot promise. A pseudonym is stable, so one appearing eighty times is
one router appearing eighty times. A radio fingerprint locates itself, because the set of
access points at a corner is that corner's identity, which is the mechanism `--locate` runs on,
so somebody with their own scan of that street can join their real addresses onto the
structure. And the shape of the walk survives the move to an artificial origin, which is what
reproducing the numbers needs and what makes the route searchable against a map.

**The written pseudonym is the HMAC, shortened.** A counter reads better, `ap-0042` against
`ap-1c8a74f992ae`, and a counter is a lie: it numbers things in the order they turn up, so the
same router exported twice gets two different names and the key decides nothing whatever. Two
exports months apart agreeing is the reason the key is kept at all, and a name that does not
depend on the key cannot deliver that. The first version of this got it wrong in exactly that
way, and the proof was short: the same walk exported under two different keys came out byte for
byte identical.

Publishing the digest is safe precisely because it is keyed. The objection to a bare
`sha256(ssid)` is that anybody can run a list of common network names through it and see which
ones match. Without the key nobody can compute the HMAC of a guess. The kind is hashed along
with the value, so a name that happens to read like an address cannot be tested against the
address space, and an address is folded to one spelling first, the way `netlog.address` folds it
on the way in.

**The notebook is rewritten and not patched.** `--geocode` keeps every byte of the notebook it
edits, comments included, because there the operator's own notes are worth keeping. Here they
are arbitrary prose about somebody's afternoon, and the only way to be certain none of it goes
out is to carry none of it: the exported notebook is built from the parsed crossings and nothing
else.

**What is not named does not leave.** Records and networks are built from a list of the keys
this knows rather than copied and patched. A field added to the log next year would otherwise
go out unread, which for a command whose whole job is deciding what may be published is the
wrong way round. For the same reason a record whose timestamp cannot be parsed is left out and
counted rather than passed through: an unshifted timestamp is the one field that would publish
the day and the hour somebody was on a particular street.

**An export is exactly one walk, and a file of several is refused.** Everywhere else in Enodia
the last walk in a file is a sensible default, and `--log walk.jsonl` reused every week means a
file can hold a year of them. Defaulting here would mean packaging up the walks nobody asked to
publish, in the one command written for publishing, so it stops and names the walks it found.
The scope reaches everything: the records, the button marks, the moment the clock is shifted
from, and the token itself.

**The association records are dropped whole.** `connected` and `disconnected` name the network
the walker was on, which is their home rather than a neighbour's, and it is the line that ties a
walk to the walker. Nothing in a reconciliation reads them, so leaving them out costs the
dataset nothing.

**Coordinates move in metres, not in degrees.** Subtracting degrees would change the cosine of
the latitude and move every east-west distance by about a percent, which on a hundred-metre
block is a metre, in a project that reports errors of eight. So the offsets are worked out in
metres and laid down again at the origin's own latitude, and the distances come out the same to
within a few centimetres.

**A type closes the fields, and only a domain closes the strings.** Serialising a typed record
means a field nobody named cannot leave. It says nothing about a field that was named and whose
type is `str`, because the value in it came out of a file that may have been edited: an `event`
of `Carlos-secret-event` and a `security` of `Carlos-private-security` both went straight out
into a published file, and the leak check did not see them either, since it was only looking
for addresses and names. So the two free strings that cross have domains. `event` is Enodia's
own vocabulary, so a value outside it is not a record of a walk and the whole record is left
out and counted. `security` is a label from a daemon, so an unrecognised one is withheld and
counted rather than the record being dropped.

The two are treated differently on purpose, and the reason is how well each domain is known.
Enodia writes every event itself, so that list is complete by construction. The security labels
are ifpeek's, and reading its source gives `open`, `wep`, `psk`, `8021x` and `secured` for the
NetworkManager and wpa_supplicant backends, while the iwd backend passes iwd's own word for the
network type through untouched and there was no way to verify that vocabulary from here. An
allowlist over a domain somebody else owns is a guess, so the failure is made loud instead: the
label does not go out, and the report says how many there were, which turns a real walk into
the thing that settles the list. If a genuine outing reports withheld labels, the values in its
log belong in `PLAIN_SECURITY`.

**The directory is the export, and that took three designs to arrive at.** An export is two
files and half of one is worse than none. Written straight out, a failure on the second left
the first behind. Written to temporaries and moved, the same thing happened one step later,
because two renames are two calls and POSIX will not make them one. Undoing the first rename
covered every error it could see and not a crash in the gap between them, and the attempt to
handle that gap was the worst version of all: it tried to tell half an export from a file
somebody had kept, by counting how many of the two names existed. It cannot be told. A walk
that was exported, read, edited and half deleted looks exactly like a crash, and the program
overwrote it. Worse, the undo then applied to a file that was there before the command ran, so
a failure during that repair deleted the operator's own file.

So the unit is the directory. Both files are written into a freshly made one beside the
destination, with an unguessable name because a predictable one in a shared directory is a name
somebody else can get to first, then fsynced, and then the whole directory is renamed into
place. One rename either happened or it did not. There is nothing half done, nothing to undo,
and nothing this could take away by mistake, because it never touches anything it did not
create. Whether the place is free stops being an inference from filenames and becomes a
question the kernel answers: `rename` refuses a destination that holds anything.

That last part also settles concurrency for free. Two exports aimed at one directory both used
to find nothing there, both built, and both reported success while only the second survived.
Now each builds its own directory and the rename decides: the first to arrive wins and the
second is told the place is taken. There is no lock, because the rename is the lock. What a
crash can still leave is a staging directory, which is litter and not half an export, since an
export is the directory this produces and that one was never given its name.

**`--out` must not exist, and `rename` is not the refusal it looks like.** Renaming a directory
onto an empty one is allowed, so a destination somebody had just made was replaced along with
its mode: `mkdir -m 700 public` followed by an export handed back 0755 without a word, on a
directory the operator had deliberately made private in order to read the export before
deciding whether to publish it. Reading that mode first and copying it onto the staging
directory looked like the fix and was two mistakes. It races, because anything can appear at
that name between the reading and the rename, and it respects only the mode: replacing the
inode drops its ACLs, its owner and its extended attributes silently. Honouring part of what
somebody set up and quietly discarding the rest is worse than not offering it at all, and
`chmod 700` on the export afterwards is one command that keeps every one of them.

So the name is claimed with `mkdir` before anything is built. That fails if anything is there,
a dangling symlink included, it is atomic so there is nothing to look at first and nothing to
race with, and it answers a second question for free: what permissions this directory should
end up with is exactly what a directory made right here with the operator's own umask gets.

**A commit that happened is reported as one that happened.** The last step of an export is
making the directory's new name durable, and that step failing used to raise. The export was
written and complete by then, so the operator was told nothing had happened while a finished
export sat on the disk, and the refusal to overwrite then stopped them running the command
again to find out. It is said instead of raised: the report names what could not be confirmed,
which is worth knowing before that export is the only copy, and does not pretend the walk was
not exported. The claim on the name is given back the same way, when the failure is early
enough that there is nothing to give back but the claim: making the directory to build in used
to happen before anything could be undone, so a full disk there left an empty claim standing
and the next run was told the destination already existed.

**The export is private while it is being written.** The staging directory used to be made with
whatever the umask gave, which is usually 0755, and only had the destination's mode applied at
the end. Everything in it was readable by anybody on the machine for as long as the writing
took, which is precisely the window that arranging to read an export privately was meant to
close. It is made 0700 and opened up at the last moment before the rename instead. Private
first and public at the end is the only order with no window in it.

**The export key is created once, which a rename cannot do.** It was written the careful way
the map is written, to a temporary and then moved into place, and that is exactly wrong for a
file whose whole value is being the only one. Two exports starting together both found no key,
both made one, and the second replaced the first. Both succeeded. But the first walk went out
pseudonymised under a key that no longer exists anywhere, so it can never be added to, and the
point of keeping a key at all is that a walk exported this month and one exported next year
agree. What the name leads to has to be a file, too. A key is 32 bytes on a disk, and opening a name
without saying so reads whatever the name happens to be: a FIFO with nobody writing to it waits
for a writer that never comes, and a character device hands over as many bytes as anyone cares
to ask for. Either one hangs a command whose job at that moment was to read half a line of
hexadecimal, which is a strange way for an export to end. The name is opened without waiting,
asked what it is through the descriptor already held rather than by its name again, and read
only once it has answered that it is a regular file. Asking through the descriptor is not
fussiness: it leaves no moment between finding out what something is and reading it in which it
could become something else.

A name is taken, or it is free, and that is not the same question as whether a file is there.
The loop asked `exists`, which follows the link and answers about the file at the end of it, so
a symlink to nothing said the name was free while `link` said it was in use, and the command
went round between the two until somebody killed it. The question is asked with `lstat` now,
and a name that is taken and does not lead to a file that can be read stops with a line saying
so rather than being waited on.

Creation is `link`, and the reason is that the two properties wanted here come from
different places. A temporary moved into place is never seen half written, and always replaces
what is there. `O_CREAT | O_EXCL` on the real name never replaces anything, and creates the name
before the bytes, so whoever loses the race can read a key of nought bytes and be told the file
is broken while it is merely unfinished. Linking gives both: the content is written and fsynced
under a temporary name, so it is complete before anything can see it, and linking that inode to
the real name either works or fails because somebody got there first. Whoever loses goes round
again and reads the key that won.

**One thing gets one pseudonym, and the rest of Enodia decides what one thing is.** A crossing
is written by hand, one outing at a time, weeks apart, so `Yaguaron` one week and `Yaguaron`
with its accent the next are one street: `folded` takes case, accents and runs of spaces out,
and the canonical frame and the confusable-name check both compare by it. The export hashed the
spelling instead. Three corners of one avenue, written `Agraciada`, `agraciada` and
`AGRACIADA`, came out as three avenues, which is not a cosmetic difference in a file whose
whole point is being reconciled again: it changes the shape of the walk that gets published.
Street and place names are folded before they are hashed. A BSSID is folded for case only, the
way `address` and `SeenNetwork.key` fold it, and an SSID is left exactly as written, because
`Casa` and `casa` are two names somebody chose rather than two spellings of one.

**The export greps itself, and reports rather than refuses.** The plan for this feature ended
with two manual steps: grep the exported files for every address, network name and street name
in the originals, and then read the export line by line. The second cannot be automated and is
not attempted. The first is the one that does not scale to a walk with three hundred networks,
so it runs on every export.

What it searches for comes from the source files and not from the pseudonyms that were handed
out, and that difference is the whole value of it. Checking the values that went through `Names`
only proves that what was substituted was substituted. The leak worth catching is the field
nobody passed through `Names` at all, which is exactly what `interface` and `reason` both were
until somebody went looking. For the same reason it reads every walk in the file and not only
the exported one, since publishing a neighbour from a walk the operator had forgotten the file
held is the worse of the two mistakes. The notebook's comments are searched for too, precisely
because the export drops them whole and nothing downstream would ever notice one coming out.

It reports and does not refuse. A substring search cannot tell a leak from a coincidence: a
network called `date` matches every directive in the exported notebook and one called `2402`
matches every frequency, and refusing on those would be an export nobody could make with no way
around it. So the block gives a name and a count, and says in as many words that it is there to
be looked at rather than believed.

## A temporary file is a name somebody else can get to first

The map is added to whole or not at all, which means writing it beside itself and moving it
into place. The obvious name for that file is the map's own with something on the end, and the
obvious name is the problem: anybody who can write in that directory can leave a symlink
waiting under it, and the next outing opens the link, pours the map through it into whatever it
points at, and then leaves the map itself as that link. It takes a few lines to reproduce and
no privilege at all beyond a shared directory.

So the temporary is made by `mkstemp` in the map's own directory: an unguessable name, created
with `O_EXCL`, which refuses a path that already exists whether it is a file or a link. It
carries the mode the map already had, and it is removed again if the move never happens. After
the move, the directory itself is flushed: the contents were on disk before the rename, and
until the directory is too, a power cut can leave the name pointing at the file it used to. The
same change stops two copies of Enodia running at once from writing over each other's
half-finished map, which the fixed name also allowed.

The log is not written this way and does not need to be. It is opened for append and never
replaced, so there is no second name and no window.

## The kernel saying it lost your input

`SYN_DROPPED` is the input layer telling a client that its queue overran and events were thrown
away. The kernel's own rule for what to do about it is to ignore everything up to the next
`SYN_REPORT` and re-read the device's state, because the stream in between is the tail of a
packet whose head is gone.

For a music player that is a formality. Here the events are street corners, so it matters twice
over. A press after the drop must not be counted, because it would put a crossing where there
was none, and an invented crossing is worse than a lost one: the person who pressed the button
notices the second and never the first. And the drop itself has to be said out loud and written
into the log as a `button_lost` record, because the mark numbers carry on afterwards as though
nothing had gone missing, so the paper and the log agree with each other and both are short a
corner. Nothing here re-reads the device state: it would say which keys are down now, and
nothing whatever about the press that went missing while the queue was full.

The boundary the kernel names is the next `SYN_REPORT`, and it does not care where a read happens
to end. A drop can arrive in one read and its `SYN_REPORT` in the next, so which devices are
still inside a broken packet is remembered between reads rather than worked out fresh each
time, and forgotten when the device goes away and its file descriptor number is free for the
kernel to hand to something else.

## What a closed screen cannot tell you

Some things go wrong in a backpack that nothing on a closed screen can tell you about, so
Enodia says them out loud. Each of these was a silent failure first.

If the laptop suspends anyway, it notices on waking: `CLOCK_BOOTTIME` keeps counting through a suspend and `CLOCK_MONOTONIC` does not, so the two drifting apart between one cycle and the next is exactly the time asleep. It says how long ("The laptop slept for 12 minutes") and writes a `suspended` record, so the hole in the log is explained rather than read later as a stretch with nothing on it.

A scan the Wi-Fi daemon refuses (not being in the `network` group is enough) is announced once, reminded once a minute while it lasts, and closed with "Scanning again" when it recovers, with a `scan_failed` record for every cycle it cost. Before this, that failure sounded exactly like a healthy walk: "Scanning", the time, "Scanning", the time, over a log filling with nothing.

## What the first real outing falsified

A radio switched off by rfkill (an airplane-mode key knocked inside the backpack, `rfkill block wifi` left on from the desk) hears nothing, which looks exactly like a street with no Wi-Fi on it. Enodia reads the kernel's switches before every scan and treats a blocked radio as a failed scan: "Radio blocked on wlan0", the reminders, a `scan_failed` record with the reason, and no scan record at all, so afterwards a hole is a hole and an empty scan is an empty street. What it does not read is the interface's operational state, and the first real outing is why: a walk is spent associated to no network, which the kernel reports as `DOWN` while forty networks are in view, and an earlier Enodia that took `DOWN` for a dead radio announced it all the way and threw every scan out as evidence of pace.

That is the only claim in Enodia so far that a real walk has settled either way, and it settled
it against the code. Everything else in the README that carries a measured result comes from
synthetic observations built to have a known answer. The bundled Agraciada example uses real
OpenStreetMap coordinates and geometry, but its radios and passes are synthetic. That is why
`--check-pace`, `--check-passes` and `--check-map` exist: they let a real outing settle the rest
the same way.

## The map is added to whole or not at all

The map lives in `$XDG_DATA_HOME/enodia/map/map.jsonl`, in a directory of its own so that `--resume` never mistakes it for an outing's log, and `--map FILE` puts it anywhere you like. Adding the same outing twice is refused rather than done, since a doubled outing pulls every answer towards itself. An outing goes on whole or not at all, written beside the map with the permissions the map already had, and moved into place, because that refusal is what makes a half-written walk unrepairable: the map would hold a third of it, call the outing present, and add nothing on a second run. The log is the opposite case and is appended line by line, since there everything already written is worth keeping.
