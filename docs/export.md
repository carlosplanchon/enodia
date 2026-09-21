# Publishing a walk without publishing a neighbourhood

The README has asked for a long time for one real outing in the repository, and doing it by
hand means publishing the Wi-Fi landscape of somebody's street. `--export-public` is the layer
that makes it possible, and most of its design is about not overstating what it does.

[The CLI reference](cli.md) says how to run it. This says what it promises, what it cannot, and
the mistake behind each rule, in the shape [the design notes](design.md) use everywhere: what it
does now, why, and the failure that asked for it.

**It is pseudonymisation.** The command prints that word every time it runs, along with the
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

**What is not named does not leave.** Records and networks are written out by naming fields off
the typed record, never copied and patched, so a field added to the log next year does not go
out until somebody writes it in. The other way round, a whitelist over whatever the file held,
let anything new leave unread, which for a command whose whole job is deciding what may be
published is the wrong way round. [The design notes](design.md) tell how the export came to read
the log the way everything else does. For the same reason a record whose timestamp cannot be
parsed is left out and counted rather than passed through: an unshifted timestamp is the one
field that would publish the day and the hour somebody was on a particular street.

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
create.

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

The claim also settles concurrency for free. Two exports aimed at one directory both used to
find nothing there, both built, and both reported success while only the second survived. Now
the name is claimed before anything is built and the claim decides: the first to arrive wins
and the second is told the place is taken. There is no lock, because the claim is the lock.
What a crash can still leave is the claimed name, empty, and a staging directory beside it,
which is litter and not half an export, since neither is the directory this produces.

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
agree.

Creation is `link`, and the reason is that the two properties wanted here come from
different places. A temporary moved into place is never seen half written, and always replaces
what is there. `O_CREAT | O_EXCL` on the real name never replaces anything, and creates the name
before the bytes, so whoever loses the race can read a key of nought bytes and be told the file
is broken while it is merely unfinished. Linking gives both: the content is written and fsynced
under a temporary name, so it is complete before anything can see it, and linking that inode to
the real name either works or fails because somebody got there first. Whoever loses goes round
again and reads the key that won.

What the name leads to has to be a file, too. A key is 32 bytes on a disk, and opening a name
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

**One thing gets one pseudonym, and the rest of Enodia decides what one thing is.** A crossing
is written by hand, one outing at a time, weeks apart, so `Yaguaron` one week and `Yaguaron`
with its accent the next are one street: `folded` takes case, accents and runs of spaces out,
and the canonical frame and the confusable-name check both compare by it. The export hashed the
spelling instead. Three corners of one avenue, written `Agraciada`, `agraciada` and
`AGRACIADA`, came out as three avenues, which is not a cosmetic difference in a file whose
whole point is being reconciled again: it changes the shape of the walk that gets published.
Street and place names are folded before they are hashed. A BSSID is folded for case only, the
way `address` and `SeenNetwork.key` fold it, and an SSID is left exactly as written.

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

