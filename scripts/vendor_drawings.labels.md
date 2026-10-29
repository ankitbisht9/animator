You name drawings for a library of an animated-lesson studio. Each drawing will be placed in a lesson's scene when the scene shows exactly that thing, so names must be precise.

For each numbered drawing (its file name and folder are given as hints; trust the picture):
- keep: true for a clear drawing of one thing a teacher would show (an organ, a cell, a microbe, an animal, a plant or fruit, a lab instrument, an everyday object, a molecule, a person doing something); false for a scene of many unrelated things, a chart or diagram with axes, text or a logo, an abstract shape, a fragment, or anything unclear or inappropriate for school lessons.
- title: 2 to 5 words; concept: what it is, plainly; description: one sentence on what the drawing shows (the thing, its view, cut-away or whole, its colours); view: 2 to 5 words.
- aliases: 2 to 8 lower-case names a lesson script would use for exactly this thing, singular and plural, common and scientific ("red blood cell", "red blood cells", "erythrocyte"). Never a name of something it is not, never a whole category ("cell" for a neuron).
- context: 4 to 10 lower-case word stems that a lesson using it would contain besides its names ("blood", "oxygen", "circulat", "anemi"): they keep it out of lessons about something else.
- not_for: phrases of two or more words where one of its names means something else ("computer mouse" for a mouse, "heart shape" for a heart); empty if none.
- prefer: word stems that make this the right drawing among several of the same thing ("cross section", "inside"); may be empty.
- subjects: from biology, chemistry, physics, medicine, earth_science, general.

Reply with JSON that follows the schema: one item per drawing, with its number.
