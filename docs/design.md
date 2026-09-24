# Why Enodia is deliberately conservative

Enodia answers a question nobody can check afterwards. You cannot go back and find out where
you really were at 17:52:10, so every answer it gives is one you have to take on trust, and the
only defence against that is a program that says what it does not know. Most of the design here
is that one rule applied in different places, and most of it was learned by getting it wrong
first. This file is the record of that: what the program refuses to do, and the mistake that
taught it to refuse.

The [README](../README.md) says what Enodia does. This says why it is like this. Each section
is one decision: what the program does now, why, and the failure that asked for it, in that
order. Reading it start to finish is not the point. Finding the one that explains a refusal you
just met is. The export has a page of its own, [Publishing a walk](export.md), since it is the
one feature with somebody else's privacy on the line and its decisions fill a page.

| If you are wondering | Read |
|---|---|
| why an answer is missing rather than approximate | *Not knowing is an answer* |
| why five scans of one pass are one witness, and a router heard everywhere counts for little | *One walk, one look* |
| why a scan after a hole in the log is not placed too early | *A hole in the scans is not a step* |
| how a scan that could be at either of two corners is placed | *A tie is settled by the walk, not by the scan* |
| why the live answer moves at a walk and not scan by scan | *A walk has a pace* |
| why an answer says it is at a corner | *A corner is somewhere too* |
| how another card is calibrated against the map on the run | *Two cards hear one street differently* |
| what `--along levels` does, and why it is experimental | *Where along the block, from the levels* |
| what `--sequence path` does, and what it costs | *The path or the point, and why it is a flag* |
| what `--check-map` holds out, and what a scan placed across a mark is | *A scan at a mark is on two stretches* |
| why a block came out on the chord with the street drawn | *A street is more than one way* |
| what the live map shows, and why an unsure answer looks unsure | *A live map, and what it may claim* |
| why an unreadable file is not an absent one | *A failure to read is not evidence of absence* |
| why a value in a log was thrown away | *Refused at the door* |
| what a record actually is, and why the export is a serialiser | *One record, and two ways of writing it down* |
| why a failed peripheral read does not stop the walk | *Nothing else is allowed to end the walk* |
| why an outing is named the way it is | *Identity, and how the clock stopped being enough* |
| why a button press is timed by the kernel and not by Enodia | *The button, and which clock a press happened on* |
| why the assistant suggests what it suggests | *Suggesting a next step without pretending to know one* |
| why a write to the map can fail without losing anything | the three sections from *The map is added to whole or not at all* |
| what an export promises and what it cannot | [Publishing a walk](export.md) |
| why a missing button press is written down | *The kernel saying it lost your input* |
| why the preflight says FAIL about a lid that is fine | *The lid*, in [setting up the machine](setup.md) |
| what a real walk changed | *What the first real outing falsified* |

The real outing the newer sections quote, a walk in Dolores on 2026-09-22, is in
[`samples/dolores/`](../samples/dolores/README.md) with its networks pseudonymised, so its
numbers can be run again: `dolores-map-check.txt` and `dolores-map-check-levels.txt` are its two
map checks.

## Not knowing is an answer

Four rules do most of the work. **Not knowing is an answer**: off the map every fingerprint is a
poor match and the best of them is still wrong, so below a floor it says "not on the map" rather
than guessing confidently in a city it has never been to. The floor is applied to each walk's
look before any of them votes, which matters more than it sounds: when a stretch won on its
fingerprints added up, four readings too weak to be evidence outvoted the one reading that
cleared the floor, and the answer came back naming the losers' street. Nothing that is not
evidence on its own becomes evidence by turning up four times.

**Two streets are never averaged**: when two stretches match about as well, both are reported,
because the midpoint of two streets is inside the block between them, where you certainly were
not. When the scan is one of a run, the scans before it can settle which of the two: see *A tie
is settled by the walk, not by the scan*.

**A stretch is one stretch whichever way you walked it**: the two crossing names are sorted
before anything is stored, and compared with case, accents and spacing folded away, so
`Yaguarón` one week and `Yaguaron` the next do not quietly become two streets that never match.

And **a crossing name means one place**: matching is done on the networks in view, which know
nothing about geography, so a map holding two towns that both have an "Artigas y Rivera" can win
with fingerprints 200 km apart, and the weighted middle of them is a field between two cities.
When the fingerprints behind the winner are further apart than three of that stretch's own
lengths (five kilometres, for a stretch whose length nobody knows), the stretch is still named
and the coordinates are withheld, with a line saying so. Crossing names have to be unique across
a map, and no map fed one outing at a time can enforce that for you.

## One walk, one look

Two things about the matching changed together, and the sample measured both.

**A network is worth what it is rare.** A router heard along thirty metres of one street says
where you are, and one heard down ten blocks hardly does, but a Jaccard over the BSSIDs in view
counted the two alike, so the long-range routers, which are also the ones every scan in the
neighbourhood shares, drowned the short. Each network now weighs `log(1 + N/df)`, `N` the
fingerprints in the map and `df` how many of them hear it: heard in every one it still weighs
`log 2` and never nothing, since it says you are on the map even if not where, and heard in one
it weighs `log(1 + N)`, seven times as much on the sample. A network the map never heard weighs
as one heard once, which is the treatment it always had: it enters the union and lowers every
match alike, and enough of them is what "too much of it has changed" is for. `--weigh alike`
turns the weights off, so that a real map can measure them the way the sample did.

**A walk speaks once.** Consecutive scans of one pass see almost the same networks and are one
look at one place, not several. Counted one by one they were: the answer was the five
fingerprints most like the scan, wherever they came from, so a pass that scanned every five
seconds filled every seat and outvoted a single better match from another outing, and the map
favoured whoever had walked slowest. Each walk of the map now gives one look, the part of it
that matched best: the scans taken within fifteen seconds of one of them, whichever such
neighbourhood has the highest mean similarity. The mean and not the best single scan, because
on a run of nearly alike scans the best one is a lucky reading until the scans beside it agree.
A walk that scanned rarely gets a look of one scan and no penalty for it.

The first version added the looks of a stretch up, as the fingerprints had been, and the sample
refused it: 29 scans across a mark where there had been 8. At a mark the two stretches match
about alike, and the one walked twice won on both sides of it, every time. A stretch is judged
by its best look, then, and two walks that agree are reported as agreeing, not counted twice.
Once a stretch is a similarity rather than a sum of seats, a tie has to be read differently
too: 86% against 69% is not a 56/44 split. A tenth of similarity is three to one, and the
shares that gives, which are what the tie and the path read, call three scans of the sample
ties, two of them at a mark. At two to one the path lagged a scan more at each mark and four
scans mid-block were called ties, and at four to one only a tie at a mark went, where a tie is
the honest answer.

Measured on the sample, the two together change nothing on the scan alone, place two more
scans right by signal, and cost the path one, a scan at a mark: a look a few metres past a
mark is barely surer than the one before it, and the path needs a margin to move. That is the
bet the path always made, made visible.

## A hole in the scans is not a step

The pace is read off how much the networks in view turn over from one scan to the next, and
turnover saturates: once nothing is shared, a step reads as 1 whether it took five seconds or
a minute. So a hole in the scans, a daemon that refused for a minute, counted as one step of
walking, and everything after it on the stretch was placed too early. On a synthetic 300 m
stretch walked at a steady pace, a 60 s hole moved the scans after it 13 m on average and 23 m
at worst, while the clock, which cannot saturate, was not moved at all. A step longer than
three of the usual ones is a hole now, and a hole is filled at the average rate of the rest of
the stretch, the way the two ends of every stretch already were.

The same filling covers a step that is not evidence. Two cards write a record each and
`merged_scans` folds them into one look, but a cycle that only one card answered stays a
one-card look, and against the two-card look beside it half the view is missing. Standing
still for four cycles with one card failing once, the scans landed at 0.2, 0.2, 0.5 and 0.8 of
the stretch, since that missing half was the only movement the stretch appeared to hold. A
step between a look one card made and a look two cards made, or between looks from different
cards, is no evidence either way, and is filled like a hole.

## A tie is settled by the walk, not by the scan

`--locate` placed one scan and nothing else: the fingerprints most like it, grouped by
stretch, and the stretch that matched best. When a second stretch matched nearly as well the
answer named both and said it was uncertain, which is honest and not much use standing at a
corner. Two corners of one neighbourhood can sound alike to a scan, the same
routers heard through the same walls from a block away, and a scan that reached the map through
`--locate LOG` had the scans before it two lines up in the same file, each of them unambiguous,
and never looked.

So a run of scans is placed by its last one, and the scans before it settle a tie. Each of them
names where it was, and of the two stretches the last scan cannot choose between, the one those
scans were on, or one sharing a mark with it, is the one the walk supports: a walk does not
jump a block in five seconds. Nothing else changes. A scan the map does not know stays unknown,
since the scans before it choose between two answers that each cleared the floor and never make
one up, and a tie they cannot break, because they were unknown too or torn the same way, stays
a tie and is reported as one. A fresh scan stands alone, having nothing before it, unless
`--locate --watch` is keeping the run, in which case each scan is placed with the ones taken
before it as they happen. And `--check-map` reports each way of using the run beside the scan
alone, in columns of their own, so that what the walk adds is measured rather than assumed.

## A walk has a pace

A scan says which stretch you are on well, and where along it less well. On the sample, 22 of
129 steps between two answers in a row moved faster than anybody walks, and in a live run that
is a dot that jumps thirty metres and back in ten seconds. So `--locate --watch` follows the
place along the stretch with a filter of constant speed, a Kalman filter in one dimension: each
scan moves the answer by how much it disagrees, weighed against how sure the walk so far is, and
the speed is never more than a walk's. The jumps went from 22 to 6, and from 5 to 2 of 55 on the
first real outing.

What it did not do is make the answer better, and that is the measurement to keep in mind. The
mean error stayed at 23 m on the sample and moved a metre on the real outing, because what a
scan gets wrong along a stretch is mostly a bias that the scans beside it share, and a filter
averages noise, not bias. It is a steadier answer, and the documentation says that and not more.
The filter never chooses the stretch either: that stays with the scans (`tie`, `path`), and the
filter only follows the place along it, taking the walk over through the mark two stretches
share and starting again from the scan after any other change, a gap of half a minute, or a
stretch of no known length.

## A corner is somewhere too

An answer used to say `between "A" and "B", 97% of the way` three metres from B, with an error
of twenty. That is a precision nobody has, and the true answer, the one somebody standing there
would give, is the corner. So an answer within 15 m of a mark is said as that mark, `at the
corner of "B"`, or `at "B"` when the mark is a place rather than a corner of two streets. The
distance was measured, not chosen: holding out each group as `--check-map` does, at walking
pace, and asking how far the scan really was from the corner the answer named.

| said at a corner within | the scan within 25 m of it, sample | first real outing |
|---|---|---|
| 10 m | 12 of 15 | 58 of 99 |
| 15 m | 21 of 26 | 126 of 200 |
| 20 m | 24 of 36 | 187 of 287 |
| 30 m | 37 of 57 | 217 of 373 |

On the sample, anything past 15 m adds corners that are wrong more often. The real outing is
flat either side, 63% at 15 m and 65% at 20 m, and it is one outing whose held-out passes begin
and end at corners, so it is the sample's curve that sets the number, and the real one that says
the number is not harmful. A stretch with no length written down takes a block to be a hundred
metres.

It is how an answer is said and nothing more. The dot on the live map stays where the estimate
is, and the reconciliation keeps the exact fraction, since there the fraction is what was worked
out and the report is about the working. One consequence is taken on purpose: two stretches that
meet at the corner an answer is at, tied, are not a doubt about where you are, and `at the
corner of "B", uncertain` said a thing and took it back. They are no longer reported as a tie.
It is rare, none on the sample and 9 of 75 uncertain answers on the real outing at walking pace,
and it is the case where the old report contradicted itself.

## Two cards hear one street differently

Two cards hear the same network several dB apart, and `--match signal` compares levels: six
decibels multiply every similarity by 0.55, `exp(-6/10)`, and a street the map knows comes back
"not on the map". Matching on which networks are in view looks at no levels at all, so a card's
offset changes nothing there, not one answer on either map; nor does it move an access point,
since the reconciliation's weights are normalised and a constant offset cancels out.

The offset can be learned on the run without being told anything, because the level-free
matching already knows where you are. Wherever it is sure, the levels this card heard are set
against the ones the map kept at that spot, and the median of the difference is the offset.
Which pairs to count is where it went wrong twice. Chosen on this card's reading, a card that
misses its faintest networks keeps only the readings that came out high, and the offset came out
short, 3 and 5 dB for a card 6 dB low. Chosen on the map's reading, the map's strong readings
were partly the luck of that day, which regresses on this one, and it came out long, 8 and 9 dB,
and 1.5 and 4 for the very card that built the map. Chosen on the mean of the two, neither
side's luck decides which pairs count, and it came out right. On the first real outing, held out
as `--check-map --card-offset` holds it, by signal:

| the card | by signal, as it reads | calibrated on the run | learned |
|---|---|---|---|
| the one that built the map | 63 / 35 / 37 m | 63 / 34 / 37 m | +0.0 dB |
| reading 6 dB higher | 64 / 66 / 35 m | 63 / 34 / 37 m | +6.0 dB |
| reading 6 dB lower | 64 / 76 / 36 m | 63 / 34 / 37 m | -6.0 dB |

On the right stretch, not on the map, and the mean error. Calibrated, the three cards come out
the same, and the map's own card is left where it was. It takes thirty pairs, the first half
minute of a walk.

Two things were tried and left out. A card that is deafer than the map's misses networks the map
has, and ignoring the map's networks below what this card can hear helped one map and hurt the
other, the sample's error going from 27 to 30 m: not a correction yet. And the offset is not
kept between runs, since it is against the card that built this map and is wrong for any other,
and learning it again costs half a minute.

A real walk was less kind to `--match signal` itself than the simulation. It lost 38 of 111
scans on streets the map knows, where matching on the networks lost none. The levels differed
from the mapping day's by anything from +4 to +19 dB from one network to the next, which no
single correction fixes, and the way the laptop was carried may be why; that is not yet known.
The run had also begun indoors, beside a router the map had only heard from the pavement, and
that taught the calibration a difference that was not the card. It is barely tested, it has so
far done worse, and it stays to be measured.

## Where along the block, from the levels

The place along a stretch is the middle of the fingerprints that matched a scan, which is only
as fine as they are. `--along levels`, which is experimental, fits instead a curve for every
network heard on the stretch, of how its level rises and falls along the block, and puts the
answer where the scan's levels fit the curves best. The stretch is still the matching's.

It began from a measurement that was wrong. Counting only the answers that landed on the right
stretch, the ones near a corner looked pulled thirty metres towards the middle of the block on
the first real outing. But near a corner the answers that went past it, into the block next
door, are exactly the ones that would have said otherwise, and leaving them out left only the
ones inside. Counted whole, and measured through the corner:

| near a corner, a fifth of the block from it | on the stretch | past the corner | together |
|---|---|---|---|
| the sample | 55, +18.8 m inwards | 8, -31.3 m | +12.4 m |
| the first real outing | 13, +33.6 m inwards | 193, -23.6 m | -20.0 m |

The sample's pull inwards is real. The real outing's answers mostly go past the corner, since 24
of its 31 blocks were walked once and holding that pass out leaves nothing on the block.
`--check-map` reports this as "pulled in, near a corner" in every column now, flag or not.

Two choices in the fit are what make it worth trying. The curves are local linear fits and not
local averages, because an average near the end of a block has data on one side only and bends
every curve towards the middle, which is the pull being taken away. And the fit is on the shape
of the levels, with the mean difference taken out at every point, so a card reading six decibels
low lands in the same place and it needs no `--match signal`. Curves that say nothing about one
point over another leave the answer where the matching put it. Held out as `--check-map` holds
them, by networks:

| | the middle of the matches | along the levels |
|---|---|---|
| the sample: mean and median error | 23 m, 21 m | 23 m, 21 m |
| the sample: pulled in, near a corner | +12 m | +11 m |
| the first real outing: mean and median error | 38 m, 35 m | 33 m, 29 m |
| the first real outing: pulled in, near a corner | -21 m | -13 m |

On the real outing it brings the answers that went past a corner back towards it, and every
column gains about five metres. On the sample it changes nothing that matters, and the sample's
radios were made by a model much like the one the curves draw, which is a reason to trust it
less, not more, where it does well. That is one real outing, held out a pass at a time, and it
is why the flag is experimental: a second outing over the same streets on another day is the
measurement that decides it, and if it does not hold there, it goes.

## The path or the point, and why it is a flag

`--sequence tie` is the rule above: the scans before the last one speak only when it cannot
choose between two stretches. A scan that is sure and wrong never asks them. Two corners of a
neighbourhood that sound alike can leave a scan 6 to 1 for the wrong one, and then three scans
unmistakably on the other street, two lines up in the same log, count for nothing.

`--sequence path` asks them every time. It is a small Viterbi over stretches: each scan's
candidates are its states, the share of the scan's evidence a candidate carries is its
emission, and a step between scans costs nothing to stay on a stretch, a little to move onto
one that shares a mark, and a lot to jump anywhere else. The answer is the last stretch of the
likeliest path, which can only be a candidate of the last scan, so nothing is invented. On the
synthetic pair of lookalike corners, the scan that was sure and wrong is put back on its
street with a share of 0.76 after one scan sure of the right one, and no surer after three,
since what they settle is where the path was, where settling ties left it wrong.

Two things had to be settled before that was honest. A scan that is torn between two corners
contributes its candidates and no lean: without that, standing at the lookalike corner for
twenty seconds turned four ties into a verdict, since four small leans the same way added up
to one where three did not. Nothing that is not evidence on its own becomes evidence by
turning up four times, here as at the floor. And the path cuts both ways. Three scans leaning
6 to 1 towards the wrong corner and then one leaning 8 to 1 towards the right street come out
on the wrong corner with a share of 0.72, overruling a scan that was right, where settling
ties would have kept it. The report says so in as many words: the scan alone would have said
the other place. A last scan that was beyond doubt is kept: overruling it would cost a jump,
and its lean is worth more than one.

That cost, and the price of a jump, which is a number nobody has measured, are why this is a
flag and not the new rule. It is the same reason `--pace` and `--match` are flags: choosing
for the operator before a real walk has measured the two would be inventing the result, and
`--check-map` reports both, in columns of their own, so that the walk can.

Where it stands: barely tested, and so far worse. It put fewer scans on the right stretch than
settling ties on the sample and on the first real outing, and walked with, it was late onto a
new block and never right where the default was wrong. It stays so that a second outing over the
same streets can measure it, and goes if that says the same.

## A scan at a mark is on two stretches

`--check-map` held out one walk, one outing down one stretch, and located its scans from the
rest. Two things were wrong with that at the ends of every stretch. The next stretch of the
same outing stayed in, and its first scan was taken five seconds after the held-out walk's
last one, a few metres further on: the copy of the question the hold-out exists to keep out.
And an answer on that next stretch, a few metres past the mark, was counted as landing on
the wrong stretch, which the report calls a different street. On two synthetic outings over
two consecutive blocks, 7% of the scans were counted as a different street, every one of them
within a step of a mark, with real errors of 5 to 13 m.

So a map with more than one outing holds out an outing at a time, and its scans are located
from the other outings only, which is the question anybody asks of a map: does it know this
street from another day. A map with a single outing still holds out a walk, since that is all
there is to hold out, and the report goes on saying that such a result is the map recognising
a walk rather than a place. And an answer on a stretch that shares a mark with the true one
is placed across a mark rather than on a different street: its error is measured through the
mark they share, in metres when both places have coordinates, and it is counted
in a row of its own.

## A street is more than one way

OpenStreetMap draws a street as many ways, and starts a new one wherever a tag changes: the
surface, the number of lanes, a bridge. `--streets` kept every way, and the block between two
marks was looked for on one way at a time, so a block whose two marks sat on different ways
of one street fell back to the chord with nothing said. The ways of one street that meet
end to end are chained into one line before a block is looked for, and the report says how
many blocks followed the drawing and how many sat on the straight line between their marks,
so that a streets file doing nothing is no longer indistinguishable from one doing everything.

Where a mark falls on the drawing had the same blind spot. The block was the run between the
vertex nearest one mark and the vertex nearest the other, within 25 m, and OpenStreetMap puts
vertices where a way bends or meets another, not along a straight run: a mark halfway down a
straight 200 m block sat 100 m from the nearest vertex and that block fell back to the chord
too. Each mark is projected onto the nearest point of the way instead, vertex or not, and the
run is cut there.

## A live map, and what it may claim

`--locate --watch --live-map` draws each answer on a page that reloads itself, over the streets,
water and parks `--geocode --surroundings` fetched once from OpenStreetMap. Not tiles: the OSM
Foundation's tile policy forbids fetching them ahead of time for offline use, and a live map is
looked at in the middle of a walk, with no connection to lean on. The data is another matter. It
is under the Open Database License, which asks to be credited, and every picture drawn from it
says so at the foot.

The harder part is the one the plan of a walk already had: a dot on real streets reads as a
fact. So an answer the scans could not settle between two stretches is drawn in another colour,
with a ring the size of how far apart its evidence lay, and when the map loses you the last
place it knew stays on the page greyed, with the words saying so, rather than vanishing or,
worse, staying the colour of an answer. The streets nobody walked are drawn and named and never
used to place anything. They are kept apart from the notebook's own because a street that runs
near both marks of a block would otherwise compete for a block it does not belong to.

Zooming it is the page's own business, a few lines of script and nothing fetched. The page is
written again and reloaded every cycle, so a zoom held in the page would be lost every five
seconds. It is kept in the address instead, which a reload keeps, and without script the page
falls back to reloading itself whole, as it did before it could zoom.

## A failure to read is not evidence of absence

The rule that comes up most often, because it has the most ways to go wrong. A file that cannot
be opened, a sysfs read that fails, a daemon that will not answer: none of those is the same as
a file with nothing in it, and reporting them as an empty answer produces a confident, wrong
result that looks exactly like a good one.

A configuration file that is there and cannot be read is the same kind of answer. The daemons
read those files with their own privileges and Enodia does not, so a drop-in in `/etc` that it
cannot open is not a file that says nothing: it is a file that may say the opposite of
everything else. Both the lid and the scan MAC lines say plainly that they could not establish
the configuration in force, rather than reporting what the readable half of it said.

The same argument is why `--streets` raises on a file it cannot read rather than falling back to
a map with no streets in it. An empty map puts every block back on the straight line between its
crossings, which is a quietly worse answer wearing the same face as a good one. A file that is
not there yet is the exception, because that is the ordinary case before `--geocode` has written
it.

And the map file has the strictest version. `read_map` treats only a missing file as an empty
map, because everything else it could fail on is a map whose contents are unknown, and locating
yourself against an unknown map is worse than saying so. Even the missing file is empty only
for the command that creates one: to `--locate` it is an error, since "not on the map" is a
sentence about the street, and on the first real outing a path with a typo in it got that
answer every cycle of a `--watch`.

## Refused at the door

Nothing in the file is taken on trust when it is read back. It is a text file that gets copied
about, cut in half and opened in an editor, and a value of the wrong type used to travel a long
way before anything noticed. A `cycle` of `"oops"` stopped the next outing from starting at all,
because the monitor reads the highest cycle in the file before its first scan and then compared
a string to a number. So every value is checked at the door, and a field that fails is dropped
rather than carried.

A measurement keeps its fraction, since a radio reporting -47.5 dBm has measured something. A
`cycle` or a mark `number` is an identity and not a measurement, so a fraction there is refused
instead of rounded: cycle 1.7 quietly becoming cycle 1 would fold a look at one place into a
look at another. And a name that is not a string is not a name, which is how an SSID stopped
arriving in the report printed as `['x']`.

A timestamp has to carry its offset from UTC, which every timestamp Enodia writes does: Python
refuses to compare a naive datetime with an aware one, so one edited line without an offset did
not cost itself, it took down every scan in the file out of the sort that puts them in order.
Being a number is not enough on its own either, so a reading also has to be one a receiver could
have taken. A signal of 100000 dBm reached the weighting, which raises ten to it, and came back
as an arithmetic error from inside an estimate. A plausible-looking 500 never raises anything
and simply wins every weighting it is in, which is the worse of the two. Both the file and the
live scan go through the same door.

The map is a file that gets copied about and edited by hand, so every number in it is checked on
the way back in rather than trusted: a latitude has to be a number and to be finite and to fall
between -90 and 90, a fraction between 0 and 1, and half a coordinate is no coordinate. JSON
will carry `NaN`, and Python reads it without complaint, and one of them poisons every average
it reaches, so it is refused at the door where it costs one line instead of turning up later as
an answer nobody can explain. The same goes for the log.

A line that cannot be right stops the reconciliation with its number and its reason rather than
being read as best it can. The 31st of February is not a date, a latitude of 999 is not a place
on the earth, `17:0 A` is a time typed wrong rather than a crossing named `17:0 A`, and
`date 2026-9-18` is a directive typed wrong rather than a crossing named after it. The latitude
matters more than it looks: the notebook is the ground truth every other check is measured
against, so an impossible coordinate fails nowhere, it produces distances and geometry that are
absurd and that look exactly as legitimate as the rest of the report. The directive is worth
spelling out too: a line with no time is a crossing that takes the next button mark, and almost
any line can be a crossing's name, so without a rule for it a typo stopped being an error and
quietly became a notebook of a different kind. And somebody who typed `@` meant to give a
coordinate, so reading the line as a crossing without one would quietly take the whole notebook
down to the answers it can give with no coordinates anywhere.

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

Reading which network a card is associated to is a
question for the daemon, and reading its rfkill switch is a file in `/sys`, and either can fail
on its own for reasons that say nothing about whether the radio works. A walk is four hours in a
bag where the only recovery is getting home. So those reads are wrapped: the failure is said out
loud and the cycle carries on scanning, and it is not written down as a failed scan, because the
scan did not fail. `--preflight` is careful in the same direction, and says `switch unreadable`
for a card it could not ask, which is not the same answer as a card that said it was blocked.

That is the other half of the same argument. Refusing bad input at the door is worth doing
because the alternative is a wrong answer. Refusing to carry on when a peripheral read fails
is not, because the alternative is four hours in a bag with nothing recorded. Which of the two
applies depends on whether the failure has anything to do with the answer.

## Identity, and how the clock stopped being enough

Three times over, the same lesson one size up. Something that was identified by when it
happened turned out to need an identity of its own, because the clock has one second of
resolution and things that are genuinely different can share a second.

A scan record carries the `cycle`, the pass of the loop it came from. Watching two interfaces
writes a record each, and they are one look at one place from one pair of feet, so a
reconciliation puts them back together before it reads the pace off them. The timestamp cannot
say this: it has one second of resolution, and a cycle whose scans land either side of a second
would come apart. The number is unique inside the file rather than inside the run, so a restart
carries on from the highest one already written instead of beginning again at one and folding
two places into one. A network is counted once per cycle and never once per instant, for the
same reason: two cycles fit inside one second of the clock, and each is a real sighting from a
real place.

The outing token answers the same question one size up: one file can hold several walks, the
clock cannot tell apart two that began in the same second, and `--resume` adopts the token
already in the file because that is the same walk continuing rather than a second one sharing
the pages. It goes on every record and not only on the scans, because the button marks need it
just as much.

A scan record is always what the radio heard: a cycle without a trustworthy scan is a
`scan_failed` record with its `reason` (`radio soft blocked (rfkill)` among them), never an
empty scan. Records that belong to one interface carry `interface`, which is what keeps scans
apart when more than one is watched. An association is recorded with its BSSID, so two
`connected` records with the same name and different BSSIDs are a roam between two access points
of one network, not a reconnection. It carries the signal in the same fields a scanned network
uses, which is what makes a roam readable afterwards: you moved because the first one was
fading.

An outing is named by when it began and by the token the walk wrote on its own scans, never by
the log's filename, since `--log walk.jsonl` reused every week appends to the same pages. Nor by
the clock alone, which is what this was: the timestamp has one second of resolution and two
walks can begin inside one, and then the map refused the second as already added and
`--check-map` held the two out together as one. It is the lesson `cycle` taught one layer down.
A log written before the token existed still falls back to the clock, the same way a log written
before `cycle` falls back to it for grouping.

A network with nothing to identify it is kept in the log and left out of all of this. A network
is known by its BSSID, or by its name when the backend gives no BSSID, which iwd sometimes does
not, and a network that also hides its name leaves neither. Two of those are not the same
network, they are two networks nothing can tell apart, and treated as one they agreed with each
other completely: a fresh scan of one anonymous router scored a perfect match against a
remembered, different one, and the map answered with a place. So they are never folded together,
never matched, never counted as turnover and never placed. A coincidence of absence is not
evidence.

A BSSID is compared in one spelling of itself for the same reason. Hexadecimal written out has
two of every letter, and that string is what one network being another is decided by, so
`AA:BB:CC:DD:EE:FF` in a hand-edited map and `aa:bb:cc:dd:ee:ff` in a fresh scan used to score
nothing in common. The SSID keeps its case, because `Casa` and `casa` are two names somebody
chose rather than two spellings of one.

A notebook is one walk's, so a reconciliation is one walk's too. The usual file holds exactly
one and there is nothing to choose. `--log walk.jsonl` reused every week is the other case, and
there Enodia reads the last walk in the file and says which one that was, with `--outing TOKEN`
to name an older one. Everything that reads a log works this way: `--map-add`, `--locate LOG`,
and `--geocode --marks LOG` too.

Reading the whole file instead was wrong in two ways that both looked like answers. The day of a
notebook without a `date` line came from the first scan in the file, so a notebook of the second
walk was read against the first walk's date and every scan of it fell outside the notebook. And
every run numbers its button marks from one, so the second walk's mark 1 took the place of the
first walk's, and a notebook of `#1` and `#2` was given the times of a walk on another day.
`--resume` counts on from this walk's marks for the same reason: an older walk in the same file
that reached mark 100 would otherwise have the next press announced as 101, while the paper in
your hand says 3.

## The button, and which clock a press happened on

The notebook's weak point is the time: heard through headphones, written by hand, one misheard
digit moves a whole stretch of the route. A headset has a button, and Linux shows it as an input
device that reports every press with the time the kernel stamped it with, which is when the
press happened rather than when anything got round to reading it. Press it at each crossing and
the time is the machine's. That stamp is what separates two presses: a thumb that bounces is one
crossing, and two real presses that arrive together after a busy moment are two. Timing them by
the reading instead made the second of those disappear. It is also what the `mark` record is
dated by, back from the moment it was handled, so two crossings two seconds apart are two
seconds apart on the paper as well.

Which clock evdev stamped it with is determined rather than assumed, by asking each clock the
machine has what time it is and seeing which one puts the press in the recent past: they cannot
both, since one counts from 1970 and the other from boot. That is what lets a press which sat in
the buffer while the laptop was busy still be written when it happened. When no clock recognises
the stamp, the last press of a batch is read as happening now, which is what every press was
read as before any of this.

Enodia answers *"Mark 7"*, and the notebook only needs the crossing's name next to that number.
The `mark` records in the log carry the times, and the reconciliation joins the two by number.
One press is one crossing, so a notebook that writes `#1` twice is refused, and so is a notebook
whose numbers the log cannot answer: that usually means the wrong walk of the log was picked,
which is what `--outing` is for. A notebook with no numbers and no log is the ordinary other
case, and it says the times are simply not known.

## What the machine says about itself

Both Wi-Fi daemons can randomise the address used for scanning, which is a different setting
from the one used once you are associated, and NetworkManager does it by default. `--preflight`
reads what yours was told and says so on the `scan mac` line, and warns when somebody has turned
it off.

It says `OK` for one thing only: NetworkManager, set once, in a section NetworkManager reads
device properties from, to a value NetworkManager documents, with no mask. A value that is
neither a yes nor a no is a warning about a value the daemon was not asked for, since reading
anything that is not an off as an on turned a typo into a green line.

A file held back by `[.config] enable=false` is skipped the way NetworkManager skips it, and one
held behind a version or environment predicate is reported as a file whose effect is not
established. The same key under `[main]` parses perfectly and does nothing, so it is reported as
doing nothing rather than as the machine's setting. A `wifi.scan-generate-mac-address-mask`
fixes some bits of the scanning address and randomises only the rest, so with one set the word
is withheld: how much of the address actually varies is not worked out here. And iwd's own
setting is never an `OK`, because it is documented as the address the interface uses, which is a
related question and not this one.

Enodia does not change it, on purpose: that needs root, which nothing else here does, and it
fights the daemon that owns the interface and will be reverted. The daemons already have a
supported way that survives reconnects and suspends, which matters when the laptop spends four
hours in a bag. So this catches rather than corrects, the way the lid and the corner names do.

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

## The map is added to whole or not at all

Adding the same outing twice is refused rather than done, since a doubled outing pulls every
answer towards itself. An outing goes on whole or not at all, written beside the map with the
permissions the map already had, and moved into place, because that refusal is what makes a
half-written walk unrepairable: the map would hold a third of it, call the outing present, and
add nothing on a second run. The log is the opposite case and is appended line by line, since
there everything already written is worth keeping.

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

The boundary the kernel names is the next `SYN_REPORT`, and it does not care where a read
happens to end. A drop can arrive in one read and its `SYN_REPORT` in the next, so which devices
are still inside a broken packet is remembered between reads rather than worked out fresh each
time, and forgotten when the device goes away and its file descriptor number is free for the
kernel to hand to something else.

## What a closed screen cannot tell you

Some things go wrong in a backpack that nothing on a closed screen can tell you about, so
Enodia says them out loud. Each of these was a silent failure first.

If the laptop suspends anyway, it notices on waking: `CLOCK_BOOTTIME` keeps counting through a
suspend and `CLOCK_MONOTONIC` does not, so the two drifting apart between one cycle and the next
is exactly the time asleep. It says how long ("The laptop slept for 12 minutes") and writes a
`suspended` record, so the hole in the log is explained rather than read later as a stretch with
nothing on it.

A scan the Wi-Fi daemon refuses (not being in the `network` group is enough) is announced once,
reminded once a minute while it lasts, and closed with "Scanning again" when it recovers, with a
`scan_failed` record for every cycle it cost. Before this, that failure sounded exactly like a
healthy walk: "Scanning", the time, "Scanning", the time, over a log filling with nothing.

## What the first real outing falsified

A radio switched off by rfkill (an airplane-mode key knocked inside the backpack,
`rfkill block wifi` left on from the desk) hears nothing, which looks exactly like a street with
no Wi-Fi on it. Enodia reads the kernel's switches before every scan and treats a blocked radio
as a failed scan: "Radio blocked on wlan0", the reminders, a `scan_failed` record with the
reason, and no scan record at all, so afterwards a hole is a hole and an empty scan is an empty
street. What it does not read is the interface's operational state, and the first real outing is
why: a walk is spent associated to no network, which the kernel reports as `DOWN` while forty
networks are in view, and an earlier Enodia that took `DOWN` for a dead radio announced it all
the way and threw every scan out as evidence of pace.

That is the only claim in Enodia so far that a real walk has settled either way, and it settled
it against the code. Everything else in the README that carries a measured result comes from
synthetic observations built to have a known answer. The bundled Rivera example uses real
OpenStreetMap corners and geometry, but its radios and outings are synthetic, and the script
beside them says exactly how. That is why
`--check-pace`, `--check-passes` and `--check-map` exist: they let a real outing settle the rest
the same way.
