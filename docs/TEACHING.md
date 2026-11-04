# How lessons teach

The research behind the teaching pipeline's prompts, and where each finding lives. Lessons are made
for learners in India (English or Hinglish narration), on any subject. Built in ADR-071 (lessons that
flow) and ADR-072 (content first); see `docs/PROMPTS.md` for the prompt files and `docs/DECISIONS.md`
for the measurements.

## Three parts of a good lesson

1. **The content**: the right ideas, in the right order, each with its reason, an example and the
   picture that shows it. Prepared first, by a subject expert (`content.v1.md`).
2. **The teaching**: the words that make that content understood (`teacher.v4.md`, checked by
   `review.v4.md` with what code measures in the words).
3. **The pictures**: what the learner sees, synchronised with the words (`director.v4.md` and the scene
   prompts).

## What the research says, and what the lessons do about it

| Finding | Source | In the pipeline |
|---|---|---|
| Teach the idea first, then its name; terms and concepts taught together raise the load | Barker, McDonnell and Wieman (concept before jargon); NCERT's science foreword ("the emphasis is not on definitions and technical terms, but on the concepts involved") | `point` before `terms` in the brief; "the idea first, then its name" in the teacher |
| Learn the parts' names and jobs before a process | Mayer, pre-training principle | process and system lessons meet their parts first; parts labelled as they are named |
| Show the misconception and correct it: dialogue and refutation videos gave about twice the gains of plain exposition (d 0.79 to 0.83), most for weaker learners; plain exposition made learners more confident in wrong ideas | Muller (PhD, University of Sydney; JCAL 2008) | `misconception` in the brief, said as a correction in statements |
| Worked examples, steps labelled by their goal, improve problem solving and transfer | Sweller (worked-example effect); Margulieux and Catrambone (subgoal labels); Rosenshine's principles | a problem's `worked` steps, each named by its goal, with a reason and a final check |
| Clarity: examples and non-examples, explanations organised in small steps | Hattie (teacher clarity, about 0.75); Marzano; Rosenshine | example and non-example where a boundary is misunderstood; one point per scene |
| An analogy must map its parts and say where it breaks | Glynn's Teaching With Analogies model | `analogy`: what maps to what, and where it stops working |
| Concrete examples help only when the link to the idea is said | The Learning Scientists (concrete examples) | "say how the example shows the idea" |
| Maths: concrete, then pictures, then symbols | Bruner; the CPA approach (Singapore maths) | maths playbook |
| Chemistry: link what you see, what particles do and the symbols | Johnstone's triangle | chemistry playbook |
| Programming: trace a concrete input step by step; predict before running | notional machine and code-tracing research; PRIMM | computing playbook |
| Conversational wording ("you", "we") helps (d about 0.79) | Mayer, personalization principle | "you, we, let's" |
| Cut what does not teach: interesting but irrelevant detail lowers learning (g = -0.33 over 58 studies) | Sundararajan and Adesope 2020; Mayer, coherence principle | "nothing unrelated", no decorative pictures |
| Narration with matching pictures at the same moment; signal what is talked about | Mayer, temporal contiguity and signaling | "show it when it is said", highlights on the key word |
| Animations help over still pictures when they show the real change (d about 0.40), not decoration | Höffler and Leutner 2007 | "show the real change" |
| Stories (a goal, an obstacle, cause and effect) are remembered | Willingham | history playbook; a process told as a journey |
| Content tied to the learner's own life raises interest and scores, most for weaker students | Hulleman and Harackiewicz (utility value) | Indian everyday contexts; a real use in the landing |
| Writing for the ear: one hearing, no rereading, so short sentences, one idea each, the noun repeated instead of "it" | broadcast writing guides | sentences of 8 to 18 words; over 22 words measured and split |
| One name for one thing: synonyms make learners think there is a difference | technical writing style guides; instructor guidance | "one name per thing" in the brief and the teacher |
| Experts skip steps they no longer see | the curse of knowledge (Camerer, Loewenstein and Weber) | "always the because"; a reviewer checks every step |
| Speech for second-language learners: about 120 to 150 words a minute | TED-Ed and caption-rate studies | (the voice's pace is a voice setting; budgets follow the measured pace) |
| Indian learners: technical terms in English, explanation in everyday Hindi | Khan Academy India; classroom code-switching studies; PW's style | Hinglish: everyday spoken Hindi, English terms explained once with "yaani" |

## What Indian platforms do

- **GeeksforGeeks** (OSI model, TCP vs UDP): a definition first; each part explained with the same
  pattern (role, data unit, example, protocols); a numbered worked example (sending an email, step 1 to
  7); a comparison as a table with both sides on the same row.
- **BYJU'S** (Ohm's law): definition, symbols and units; a water-pipe analogy with its reason ("since we
  can't see electrons"); worked examples in a fixed pattern; applications and limitations.
- **Vedantu** (hybridization): a repeatable template, but terms left undefined, no analogy, no "why":
  quick to revise from, hard to learn from.
- **Cuemath** (quadratics): a real-life hook (a rocket's path), terms defined as they appear, step-by-step
  examples with a boxed answer, tips.
- **Physics Wallah and Khan Academy India**: everyday Hindi with English terms, simple analogies, real-life
  connections; Khan Academy India reports demand for bilingual content even in English-medium schools.

The lessons take the structure these platforms share (one pattern per part, a worked example in
numbered steps, a side-by-side comparison) and add what the research says the written pages often
lack in a video: the idea before the definition, the reason for every step, the misconception said
and corrected, and pictures that move with the words.

## What a lesson never does

- Show the lesson's outline, a list of its steps or a progress tracker inside the video: the lesson page
  already shows every scene with its title and time beside the video.
- Ask more than one question in a short lesson, or open with a question by habit.
- Define what the learner already knows, or bring in more new terms than the length holds (about 2 in a
  minute, 4 in two minutes).
- Squeeze content into fragments to fit the time: what does not fit is left out.

## Sources

- R. E. Mayer, multimedia learning principles: [Dartmouth summary](https://services.dartmouth.edu/TDClient/1806/Portal/KB/PrintArticle?ID=171655), [Lucidea](https://lucidea.com/blog/mayers-12-principles-of-multimedia-learning)
- Pre-training principle: [Raspberry Pi Foundation quick read](https://static.raspberrypi.org/files/curriculum/quickreads/QR33_Mayers_Principles_2_21_05_2026.pdf)
- Rosenshine's principles: [InnerDrive guide](https://www.innerdrive.co.uk/blog/guide-rosenshine-10-principles/), [Structural Learning](https://www.structural-learning.com/post/rosenshines-principles-a-teachers-guide)
- D. Muller, misconceptions in science videos: [THE Journal](https://thejournal.com/articles/2015/05/05/watching-videos-does-not-necessarily-lead-to-learning.aspx)
- Teacher clarity, examples and non-examples: [Corwin](https://corwin-connect.com/2017/10/four-rungs-ladder-teacher-clarity/)
- Teaching With Analogies: [CSUN science teaching series](https://www.csun.edu/~vceed002/books/sourcebook/chapters/10-analogies/teaching-analogies.html)
- Concept before jargon: [Barker, McDonnell and Wieman](https://sei.ubc.ca/bitstream/seima/2132/1/Barker-McDonnell-Wieman_ConceptJargon_talk_SABER2014.pdf)
- Subgoal labels: [Margulieux and Catrambone](https://scholarworks.gsu.edu/server/api/core/bitstreams/6eae90cc-3245-48aa-b8c6-e562678bdcb8/content)
- Concrete examples and dual coding: [The Learning Scientists](https://www.learningscientists.org/learning-scientists-podcast/2018/1/3/episode-10-concrete-examples)
- Seductive details: [WSU news on Sundararajan and Adesope](https://news.wsu.edu/2020/03/19/seductive-details-inhibit-learning/)
- Animation versus static pictures: [Höffler and Leutner 2007](https://www.leibniz-ipn.de/en/research/publications/instructional-animation-versus-static-pictures-a-meta-analysis)
- Stories: [Tes on Willingham](https://www.tes.com/news/tell-it-story-if-you-want-pupils-learn)
- Utility value: [Hulleman et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC4610746)
- Johnstone's triangle: [RSC Education](https://edu.rsc.org/feature/improve-students-understanding-with-johnstones-triangle/4019740.article)
- CPA approach: [Maths — No Problem!](https://mathsnoproblem.com/en/approach/concrete-pictorial-abstract)
- Code tracing: [Teach Computing quick read](https://static.teachcomputing.org/pedagogy/QR14-Code-tracing.pdf)
- Video engagement: [Guo, Kim and Rubin 2014](https://www.cs.rochester.edu/hci/pubs/pdfs/edX-MOOC-video-production-and-engagement_LAS-2014.pdf)
- Speech rate for learners: [Western Sydney University, caption rate study](https://researchers.westernsydney.edu.au/en/publications/effect-of-caption-rate-on-the-comprehension-of-educational-televi/)
- Writing for the ear: [journalism.university](https://journalism.university/broadcast-and-online-journalism/effective-radio-reporting-crafting-stories-ear)
- Consistent terminology: [DataCamp instructor guidance](https://instructor-support.datacamp.com/en/articles/2340228-dealing-with-synonyms)
- Curse of knowledge: [UBC](https://blogs.ubc.ca/bionews/2022/03/overcoming-the-curse-of-knowledge/)
- Hinglish and bilingual content in India: [Central Square Foundation on Khan Academy India](https://www.centralsquarefoundation.org/articles/mother-tongue-education-khan-academy), [JELT study](https://journals.eltai.in/jelt/article/view/JELT670480)
- Indian learning outcomes: [Deccan Herald on ASER](https://www.deccanherald.com/education/higher-rungs-of-learning-ladder-out-of-reach-2889626)
- Platform pages studied: [GeeksforGeeks OSI model](https://www.geeksforgeeks.org/computer-networks/open-systems-interconnection-model-osi/), [GeeksforGeeks TCP vs UDP](https://www.geeksforgeeks.org/computer-networks/differences-between-tcp-and-udp/), [BYJU'S Ohm's law](https://byjus.com/physics/ohms-law/), [Vedantu hybridization](https://www.vedantu.com/chemistry/hybridization), [Cuemath quadratics](https://www.cuemath.com/algebra/quadratic-equations/)
