# stable-diffusion-webui-embedding-merge

'''
WebUI Dependencies:
    
1) Class <modules.textual_inversion.textual_inversion.Embedding> is used to create embeddings.
     required fields: <.vec> = actual tensor, <.vectors> = first dim size, <.shape> = last dim size.
2) Object <modules.sd_hijack.model_hijack.embedding_db> is abused to create ephemeral embeddings.
     Work with fields <.word_embeddings> and <.ids_lookup> is replicated from
     </modules/textual_inversion/textual_inversion.py>, refer to register_embedding() here.
     UPD: not needed anymore, since upstream implemented register_embedding_by_name()
3) Saving of embedding is done by calling <modules.textual_inversion.textual_inversion.create_embedding(name, num_vectors_per_token, overwrite_old, init_text='*')>
     and then editing .pt file, plus <modules.sd_hijack.model_hijack.embedding_db.load_textual_inversion_embeddings()>
     also <modules.sd_hijack.model_hijack.embedding_db.add_embedding_dir(path)> is used.
4) <modules.sd_hijack.StableDiffusionModelHijack.get_prompt_lengths(text)> is hooked but not replaced.
5) Part of encode_embedding_init_text() from sd_hijack_clip.py and sd_hijack_open_clip.py is converted to
     tokens_to_vectors() here; it uses shared.sd_model.cond_stage_model.wrapped and then call either
     .model.token_embedding.wrapped() for SD2, or .transformer.text_model.embeddings.token_embedding.wrapped() for SD1
6) Code from <https://github.com/AUTOMATIC1111/stable-diffusion-webui-tokenizer> is heavily copied:
     it grabs <shared.sd_model.cond_stage_model.wrapped> and checks it against
     <FrozenCLIPEmbedder> and <FrozenOpenCLIPEmbedder>, refer to tokens_to_text() here.
7) <shared.sd_model.cond_stage_model.tokenize_line(line)> is called many times when parsing prompts.
     The code is very dependent on what it returns! Source in </modules/sd_hijack_clip.py>
     Also <shared.sd_model.cond_stage_model.tokenize()> can be called.
'''

import re
import os
import torch
import json
import html
import traceback
import threading
import gradio
import modules
from modules import shared, scripts, script_callbacks, devices
from modules.shared import opts, cmd_opts
from modules.textual_inversion.textual_inversion import Embedding
from ldm.modules.encoders.modules import FrozenCLIPEmbedder, FrozenOpenCLIPEmbedder
import open_clip.tokenizer

def _webui_embedding_merge_():

    class Exception_From_EmbeddingMergeExtension(Exception):
        pass
    class Exception_From_EmbeddingMergeExtension_():
        def __init__(self,_):
            self._ = _
        def __getattr__(self,_):
            raise Exception_From_EmbeddingMergeExtension(self._)

    def gr_tab():
        with gradio.Blocks(analytics_enabled=False) as block:
            gradio.HTML('<style>#tab_embedding_merge_extension p::before,#tab_embedding_merge_extension p::after,#tab_embedding_merge_extension code::before,#tab_embedding_merge_extension code::after{display:none!important}</style>')
            with gradio.Row():
                with gradio.Accordion('Слава Україні   (Натисніть тут, щоб отримати інструкції з використання)', open=False):
                    with gradio.Accordion('Introduction...', open=False):
                        gradio.Markdown('''
## мета:

Чи знаєте ви, що StableDiffusion читає вашу підказку за так званими токенами? Вони являють собою багатовимірні числові вектори, які утворюють разом слова та фрази.

Насправді можна створювати нові слова простим об’єднанням (додаванням) різних векторів разом, у результаті чого може означати обидві речі одночасно!

Однак це не завжди працює, а іноді не дає того, чого ви очікуєте, але експериментувати точно варто.

По суті, це розширення створюватиме вбудовані текстові інверсії виключно шляхом злиття маркерів (без будь-якого навчання на фактичних зображеннях!) або автоматично під час створення, або вручну на вкладці.

## Використання:

Вкладку `Token T` можна використовувати щоб:
- перевірити свій промт або конкретні слова
- створювати ембендінг -"Текстові інверсії"  з фрагментів тексту з об'єднанням або без нього
- перевірити правильність своїх виразів злиття
''')
                    gradio.Markdown('''
### TL;DR:

Використовуй синтаксис `'одна річ'+'інша річ'`, щоб об'єднати терміни  в одне вбудовування у ваші позитивні чи негативні підказки під час виконання.

Також використовуй ` 'слово'*0,5 ` (або будь-яке число, за замовчуванням 1,0), щоб збільшити або зменшити суть 'ваших слів' (яке може бути навіть нульовим, щоб вимкнути цю частину промту).

Щоб привернути увагу за допомогою круглих дужок ( ), поставте їх навколо, наприклад `( 'one'+'two' :0.9)`
Використовуйте в одній підказці скільки завгодно; також ви можете розмістити свої існуючі імена вбудованих TI всередину `' '`.

Якщо з якоїсь причини вам потрібен буквальний ', поставте між ними пробіл.
Якщо якесь інше розширення заважає цьому синтаксису, змініть кутові дужки на фігурні: `{'also works'*4}`

## Перегляд тексту або вбудованих векторів

Ви можете вставити промпт (без будь-якого іншого спеціального синтаксису) у текстове поле на цій вкладці , щоб побачити, як воно аналізується WebUI. Усі виявлені вбудовані текстові інверсії буде вилучено та представлено вам разом із літеральними текстовими маркерами. Наприклад:

 міжгалактичний поїзд, шедевр, Дань Віµ
''')
                    with gradio.Accordion('Детальніше про стовпці таблиці та групування її рядків...', open=False):
                        gradio.Markdown('''
### Рядки:

- `By none` = інтерпретувати підказку в цілому, вилучаючи всі символи з реальних токенів
- `By comma` = розділити підказку тегами на коми, видаляючи коми, але зберігаючи вихідні пробіли
- `По частинах` (за замовчуванням) = розділити на вбудовування TI, з’єднуючи частини тексту разом, зберігаючи пробіли
- `За словами` = розділити лише після токенів, які фактично створюють пробіл у кінці
- `За токенами` = розділити на все, крім символів, які представлені більш ніж одним вектором
- `За векторами` = показати всі розділені вектори, навіть для вбудованих TI

### Стовпці:

- `Індекс` = індекс одного вектора або діапазону індексів (включно) для цього рядка
- `Вектори` = кількість кінцевих векторів для цього рядка (щоб це було чітко видно)
- `Текст` = оригінальний або відтворений з тексту токенів, узятий у лапки для ясності
- `Token` = список номерів токенів CLIP, які представляють цей рядок; для вбудовування TI * або *_X, де X — індекс поточного вектора вбудовування
- `Min` = найменше (негативне) значення вектора або значень згрупованих векторів
- `Макс` = найбільше значення
- `Sum` = сума всіх значень зі знаком
- `Abs` = сума модулів кожного значення, без знака (завжди додатне)
- `Len` = довжина вектора в нормі L2, квадратний корінь із суми квадратів значень (обчислено приблизно)
- `Std` = стандартне відхилення для векторних значень.
### Навіщо це потрібно:

Щоб переконатися, що ваше підказка інтерпретується так, як ви очікуєте (наприклад, виявлення наявних вбудовувань TI). Також ви можете вивчити токени CLIP таким чином.

Якщо ви введете нове ім’я в текстове поле внизу, увесь ваш поточний запит буде перетворено на одне вбудовування текстової інверсії з таким ім’ям (і збережено в підкаталозі `/embeddings/embedding_merge/`). Ви можете використовувати це для:

- Створення скороченої частини для швидкого використання в підказках (хоча не рекомендується, оскільки пізніше ви втратите оригінальний текст), але без інших переваг;
- Підготуйте вбудовування TI до фактичного навчання, використовуючи існуючі вбудовування для його ініціалізації.
''')
                    gradio.Markdown('''
## Перевірити вираз злиття:

На цій вкладці   ви можете ввести `'вираз злиття'`, який починається з однієї лапки, щоб побачити, як це розширення аналізуватиме та об’єднує його. Він повинен містити одинарні лапки навколо літеральних текстів або вбудовування TI та спеціальні оператори між ними. Наприклад:

 `'greg rutkowski'/4+'gustav dore'*0,75`
''')
                    with gradio.Accordion('Докладніше про синтаксис виразу злиття...', open=False):
                        gradio.Markdown('''
### Синтаксис:

- `'один' + 'два'` = змішування векторів простою сумою всіх значень. Якщо довжина відрізняється, найменша частина буде доповнена праворуч нулями.
- `'one' - 'two'` = як вище, але віднімання. Зверніть увагу, що + і - можна поставити лише між текстовими частинами та матимуть найнижчий пріоритет.
- `'текст' * NUM` = помножити всі вектори літералу в лапках на числове значення. Можна використовувати числа з плаваючою комою (0,85) і від’ємні числа (-1), але не арифметичні вирази.
- `'текст' / NUM` = ділення на число, як і множення вище. Застосовується до попереднього текстового літералу, але після попередніх подібних операцій, тому ви можете множити та ділити разом (*3/5)
- `'текст' : NUM` = змінити кількість векторів літералу, щоб зменшити або збільшити (доповнений нулями). Тільки ціле число без знака!
- `'text' :+ NUM` і `'text' :- NUM` = кругове обертання векторів у цьому маркері, наприклад, +1 зсуне індекс кожного вектора на одиницю вперед, переходячи на останній.
- `'текст',NUM` (з'єднується як `'a',B,'c','d',E,F…`) = об'єднати текст із токеном за його числовим індексом (тому, щоб отримати будь-який чистий токен – використовуйте порожній лівий рядок: `',256`). Спеціальні токени: `0000` = 'початковий токен' (індекс 49406), `000` = 'кінцевий токен' (індекс 49407), `00` = 'доповнювальний токен' (також 49407 для SD1, але 0 для SD2). Токен номер `0` не є нуль-вектором, але чомусь вважається символом `!' без пробілу після нього, який нормально ввести неможливо.

Щоб застосувати множення (або ділення), обрізання або зсув **до результату** додавання (або віднімання), ви не можете використовувати круглі дужки; натомість спробуйте цей синтаксис:

- `'one' + 'two' =* NUM` = помножить суму 'one' і 'two', але не тільки 'two'
- `'one' + 'two' =/ NUM` = розділити суму (або будь-яку кількість сум ліворуч), фактично 'результат' всього
- `'one' + 'two' =: NUM` = обрізати або збільшити результати
- `'one' + 'two' =:+ NUM` або `'one' + 'two' =:- NUM` = повернути результат

Таким чином, такі операції виконуються так само:

 `'a'/2 + 'b'/2 + ':1 - 'd'`
`'a'+'b' =* 0,5 + 'c'*0 + 'd'*-1`

Немає справжнього оператора «конкатенації» (оскільки пізніше ви зможете об’єднати кілька окремих виразів злиття), але ви можете відтворити його, додавши той самий текст, збільшений і зміщений, якщо потрібно.
Операція ',' має найвищий пріоритет (вона безпосередньо створить рядок перед тим, як робити щось інше), тому ви не можете об'єднати нічого з результатом додавання або множення. Використовуйте його лише для додавання токенів за індексом у ваш текст.

Наприклад, повторення двовекторного слова, в результаті чого виходить 4 вектори з двох рівних пар:

  'artstation' + 'artstation' :4 :+2
  'artstation','artstation'

Ви можете використовувати зсув, щоб об’єднати кілька векторів одного тексту. Наприклад, маючи 4-векторне слово, ви можете об’єднати ці вектори в один:

  'кувшинов' + 'кувшинов':-1 + 'кувшинов':-2 + 'кувшинов':-3 =: 1
  ',1836 + ',85 + ',43074 + ',341

Зауважте, що ці індекси посилаються на «ku|v|shino|v[пробіл]» і не можуть бути введені з необробленого тексту, оскільки він буде розібраний як «ku[пробіл]», «v[пробіл]» і «shino[пробіл]», які є різними лексемами!

Коли ви об’єднуєте рядки різної довжини, найкоротший доповнюється нульовими векторами; якщо ви хочете доповнити його чимось іншим, вам слід перевірити кількість векторів і об’єднати відповідно:

  'close-up',00,00 + 'out-of-frame' + 'cropped',00,00,00,00
  'up',00,00+'of-frame'+',00,00,00 =:5:+2 + 'close-'+'out-'+'cropped',00

### Навіщо воно тобі:

Щоб підготувати свій вираз і виправити всі помилки. Ви можете оцінити його правильність, приблизно порівнявши числа в таблиці (наприклад, додавання векторів, як правило, призведе до більшого значення `Abs`; тоді як множення безпосередньо змінює всі числа прямолінійно).

Якщо з якоїсь причини ви не можете використовувати синтаксис для підказок злиття під час виконання, принаймні ви зможете ввести ім’я та створити звичайне вбудовування TI із вашого виразу злиття. Тоді ви можете використовувати його навіть без цього розширення!

Також ви можете перевірити числові параметри вашого навченого текстового вбудовування та порівняти його з «нормальними» векторами. Наприклад, дуже великі `Len` або `Std` означатимуть, що щось не так, і принаймні ви можете розділити це, щоб спробувати виправити.
''')
                    gradio.Markdown('''
## Кілька виразів злиття в підказці:

Якщо ви розміщуєте дійсний вираз злиття, укладений у кутові «…» … або фігурні дужки {’…» …} у будь-якому місці підказки (без пробілу між ` ` або `{` і `'`) на вкладці EM, його буде проаналізовано та об’єднано в одне тимчасове вбудовування текстової інверсії, яке замінить сам вираз. Отриманий запит буде об’єднано з цих вставок і будь-чого між виразами. Наприклад:

 Фотографія 'кішка'+'собака', {'4k'+'динамічне освітлення'+'наукова фантастика'=/3} шедевр
''')
                    with gradio.Accordion('Більше прикладів використання кутових/фігурних дужок...', open=False):
                        gradio.Markdown('''
### Ще приклади:


Поєднання різних предметів або стилів разом, що призводить до об’єднаних концепцій:

  Реалістична фотографія 'дівчинки' + 'ляльки' у веселковій сукні, яка стоїть на березі.
Мистецтво від 'greg rutkowski'*X+'hayao miyazaki'*Y стиль.

Примітки:
- Найкраще працює, коли всі ваші об’єкти мають однакову кількість векторів (тоді можна навіть змоделювати оператор BREAK: `… фото дівчини у веселці … BREAK … фото ляльки у веселці …`);
- Вам не потрібно ділити на кількість доданих частин, особливо якщо ваші теми дуже різні (наприклад, не містять однакових жетонів);
- Перемножуючи кожну частину у другому прикладі (де X і Y є числами від 0,0 до 1,0), ви можете отримати зважену комбінацію або інтерполяцію.

Зміна ваги окремих слів у підказці:

  «Павич»*X стоїть на вершині «жирафа»*Y.
найгірша якість, потворний, 'погана анатомія',:0 розмитий, обрізаний

Де X і Y будуть числами від 0,0 до 1,0 або навіть вище, до 5. Таким чином ви можете безпосередньо змінити відносну прихильність між суб’єктами.

Примітки:
- Часто значення між 0,5 і 1,5 насправді нічого не змінюють, виглядаючи як звичайне 1,0
- Значення, нижчі за 0,5 і близькі до 0,0, справді значно зменшують вагу об’єкта! Аж до його повної відсутності (що інакше неможливо, наприклад, навіть нульова увага `(word:0)` не видаляє слово з підказки)
- Високі цифри можуть збільшити присутність об'єкта не кількісно, ​​а по суті. Дуже високі множники (вище 10) псують об’єкт, але не руйнують саме зображення.

Усунення частини негативної підказки шляхом обнулення її векторів може бути використано для розуміння ефекту відповідної частини, не зміщуючи решту тексту інакше. Оскільки WebUI розбиває довгі підказки на довільні коми (а потім об’єднує отримані частини), просте видалення частини може серйозно змінити ситуацію.
''')
                    gradio.Markdown('''
## Використання виразів злиття в підказках під час виконання!

Ви фактично можете помістити вирази злиття в кутові або фігурні дужки в запит txt2img або img2img у WebUI. Це розширення перехоплюватиме як основні, так і негативні підказки, аналізуватиме та об’єднуватиме вирази, створюючи тимчасові вбудовування TI, які WebUI «бачить» замість вашого вихідного тексту. В інформації про генерацію будуть внутрішні безглузді імена, як-от «EM_1», але додатковий параметр «EmbeddingMerge» міститиме оригінальні вирази злиття. Щоб швидко відновити ваші підказки, просто вставте повну інформацію про генерацію (з .txt або PNG Info) у текстове поле на вкладці EM (також це має працювати для офіційної кнопки «вставити» на панелі інструментів) – її тимчасові вставлення буде замінено назад виразами, наприклад:

  фото 'EM_1'
Негативне повідомлення: {'EM_2'}
Кроки: 8, вибірка: DPM++ 2M Karras, масштаб CFG: 7, вихідний код: 1374372309, розмір: 512x512, хеш моделі: c6bbc15e32, модель: sd-v1-5-inpainting, EmbeddingMerge: ' 'EM_1' = 'sky' * 2/4 + 'forest' * 3/4 ​​, {'EM_2 '}={'blurry'+'cropped'}', вага умовної маски: 1
''')
                    with gradio.Accordion('Limitations...', open=False):
                        gradio.Markdown('''
### Що не працює:

#### Прив'язка властивостей до об'єктів:

  Фото «блондина» + «хлопця» в «червоній» + «сорочці», одягненого в «зелені» + «штани» та «сині» + «черевики»

– призводить до чого завгодно, але не до того, що запитувалося.

#### Згортання виконавців до одного токена:

  Картина «Вільям» + «-» + «Адольф»+«Адольф»:+1 + «Бугро»+«Бугро»:+1+«Бугро»:+2 =:1 . Дівчина, шедевр

– призводить до чогось, що мало відрізняється від повного обнулення терміну.

#### Віднімання понять як у word2vec:

  Повна фотографія 'король'-'чоловік'+'жінка'
Детальне фото «жовто-червоного» автомобіля

– загалом призводить до повної руйнації композиції.

#### Імітація негативного підказки через заперечення слів:

  Портрет принцеси. 'рамка, чорно-біла'*-1
Кішка женеться за собакою. '-'дорога'-'трава'

– усе одно додасть ці поняття до позитивного підказки, але з дивною присутністю. Хоча вам може пощастити з малими значеннями `-0.1-0.0`.
''')
            with gradio.Row():
                gr_text = gradio.Textbox(value='', lines=4, max_lines=16, interactive=True, label='Ваш промт (без ваги/уваги, без дужок/дужок); або ваш вираз злиття (якщо перший символ є одинарними лапками); або інформацію про покоління для відновлення запитів')
            with gradio.Row():
                with gradio.Column(scale=1):
                    gr_button = gradio.Button('Parse!',variant='primary')
                with gradio.Column(scale=3):
                    gr_radio = gradio.Radio(choices=('By none','By comma','By parts','By words','By tokens','By vectors'), value='By parts', type='index', interactive=True, label='Згрупувати/розділити таблицю за: (якщо не починається з одинарних лапок, тому лише для підказок, а не для злиття)')
            with gradio.Box():
                gr_html = gradio.HTML(label='out')
            with gradio.Row():
                gr_true = gradio.Checkbox(value=True,visible=False,show_label=False)
                gr_false = gradio.Checkbox(value=False,visible=False,show_label=False)
                gr_name = gradio.Textbox(value='', lines=1, max_lines=1, interactive=True, label='Введіть тут назву для вашого нового вбудовування, яке зберігатиме результат наступного аналізу/об’єднання за допомогою кнопки вище: (необов’язково; видаляється в разі успіху)')
            gr_button.click(fn=gr_func, inputs=[gr_name,gr_text,gr_radio,gr_true], outputs=[gr_html,gr_name,gr_text], show_progress=False)
            gr_radio.change(fn=gr_func, inputs=[gr_name,gr_text,gr_radio,gr_false], outputs=[gr_html,gr_name,gr_text], show_progress=False)
        return [(block,'Token T','embedding_merge_extension')]

    def tokens_to_text():
        try:
            # https://github.com/AUTOMATIC1111/stable-diffusion-webui-tokenizer
            class VanillaClip:
                def __init__(self, clip):
                    self.clip = clip
                def vocab(self):
                    return self.clip.tokenizer.get_vocab()
                def byte_decoder(self):
                    return self.clip.tokenizer.byte_decoder
            class OpenClip:
                def __init__(self, clip):
                    self.clip = clip
                    self.tokenizer = open_clip.tokenizer._tokenizer
                def vocab(self):
                    return self.tokenizer.encoder
                def byte_decoder(self):
                    return self.tokenizer.byte_decoder
            clip = shared.sd_model.cond_stage_model.wrapped
            if isinstance(clip, FrozenCLIPEmbedder):
                clip = VanillaClip(shared.sd_model.cond_stage_model.wrapped)
            elif isinstance(clip, FrozenOpenCLIPEmbedder):
                clip = OpenClip(shared.sd_model.cond_stage_model.wrapped)
            else:
                return None
            vocab = {v: k for k, v in clip.vocab().items()}
            byte_decoder = clip.byte_decoder()
            def _tokens_to_text(tokens):
                nonlocal vocab, byte_decoder
                code = []
                ids = []
                current_ids = []
                class_index = 0
                def dump(last=False):
                    nonlocal code, ids, current_ids
                    words = [vocab.get(x, '') for x in current_ids]
                    try:
                        word = bytearray([byte_decoder[x] for x in ''.join(words)]).decode('utf-8')
                    except UnicodeDecodeError:
                        if last:
                            word = '<ERR>' * len(current_ids)
                        elif len(current_ids) > 4:
                            id = current_ids[0]
                            ids += [id]
                            local_ids = current_ids[1:]
                            code += [([id], '<ERR>')]

                            current_ids = []
                            for id in local_ids:
                                current_ids.append(id)
                                dump()
                            return
                        else:
                            return
                    word = word.replace('</w>', ' ')
                    code += [(current_ids, word)]
                    ids += current_ids
                    current_ids = []
                for token in tokens:
                    token = int(token)
                    current_ids.append(token)
                    dump()
                dump(last=True)
                return [c for c in code if len(c[0])!=0]
            return _tokens_to_text
        except:
            traceback.print_exc()
            return None

    def str_to_escape(line):
        res = re.sub(r'([()[\]\\])',r'\\\1',line)
        return res

    def text_to_vectors(text):
        dv = None
        dt = None
        try:
            res = []
            text = text.lstrip().lower()
            clip = shared.sd_model.cond_stage_model
            tokens = clip.tokenize_line(str_to_escape(text))
            count = tokens[1]
            tokens = tokens[0][0]
            fixes = tokens.fixes
            if count>=len(tokens.tokens):
                return None
            tokens = tokens.tokens[1:count+1]
            start = 0
            for fix in fixes:
                name = fix.embedding.name.lower()
                tensor = fix.embedding.vec
                num = fix.embedding.vectors
                off = fix.offset
                if num!=tensor.size(0):
                    return None
                lenname = len(name)
                if off!=start:
                    test = 0
                    while True:
                        pos = text.find(name,test)
                        if pos<0:
                            return None
                        test = pos+lenname
                        sub = text[0:test]
                        part = clip.tokenize_line(str_to_escape(sub))
                        cnt = part[1]
                        part = part[0][0]
                        vec = off-start
                        need = tokens[start:off+num]
                        if part.tokens[1:cnt+1]==need:
                            trans = clip.encode_embedding_init_text(text,vec)
                            t = trans[:vec].to(device=devices.device,dtype=torch.float32)
                            res.append((t,sub[:pos],need[:vec]))
                            text = text[pos:]
                            start = off
                            break
                if text[0:lenname]!=name:
                    return None
                tensor = tensor.to(device=devices.device,dtype=torch.float32)
                res.append((tensor,name,None))
                start += num
                text = text[lenname:].lstrip()
            if text!='':
                part = clip.tokenize_line(str_to_escape(text))
                cnt = part[1]
                part = part[0][0]
                need = tokens[start:]
                if part.tokens[1:cnt+1]!=need:
                    return None
                trans = clip.encode_embedding_init_text(text,999)
                trans = trans.to(device=devices.device,dtype=torch.float32)
                res.append((trans,text,need))
            return res
        except:
            traceback.print_exc()
            return None

    def text_to_tokens(text):
        try:
            tokens = shared.sd_model.cond_stage_model.tokenize([text])[0]
            return tokens
        except:
            return None

    def tokens_to_vectors(arr):
        old = opts.CLIP_stop_at_last_layers
        opts.CLIP_stop_at_last_layers = 1
        try:
            clip = shared.sd_model.cond_stage_model.wrapped
            if hasattr(clip,'model') and hasattr(clip.model,'token_embedding'):
                tensor = torch.tensor(arr,dtype=torch.int,device=devices.device)
                tokens = clip.model.token_embedding.wrapped(tensor).to(devices.device)
            else:
                token_embedding = clip.transformer.text_model.embeddings.token_embedding
                tensor = torch.tensor(arr,dtype=torch.int,device=token_embedding.wrapped.weight.device)
                tokens = token_embedding.wrapped(tensor).to(devices.device)
            opts.CLIP_stop_at_last_layers = old
            return tokens
        except:
            opts.CLIP_stop_at_last_layers = old
            traceback.print_exc()
            return None

    def to_float(num):
        if num is None: 
            return None
        try:
            return float(num)
        except:
            return None

    def to_int(num):
        if num is None: 
            return None
        try:
            return int(num)
        except:
            return None

    def grab_vectors(text):
        try:
            res = text_to_vectors(text)
            if res is None:
                return None
            if len(res)==0:
                res = text_to_vectors(',')[0][0][0:0]
                return res
            res = torch.cat([ten[0] for ten in res]);
            return res
        except:
            return None

    reg_clean = re.compile(r'\s+')
    reg_oper = re.compile(r'(=?)(?:([*/,])([+-]?[0-9]*(?:\.[0-9]*)?)|:([+-]?)(-?[0-9]+))')

    def merge_parser(text,only_count):
        clip = shared.sd_model.cond_stage_model.wrapped
        vocab = None
        def check_vocab(token):
            nonlocal vocab
            if vocab is None:
                if isinstance(clip, FrozenCLIPEmbedder):
                    vocab = clip.tokenizer.get_vocab()
                elif isinstance(clip, FrozenOpenCLIPEmbedder):
                    vocab = open_clip.tokenizer._tokenizer.encoder
                else:
                    return True
                vocab = {v: k for k, v in vocab.items()}
            return token in vocab
        orig = '"'+text+'"'
        text = text.replace('\0',' ')+' '
        length = len(text)
        arr = []
        left = 0
        quot = False
        join = False
        while left<length:
            pos = text.find("'",left)
            if pos<0:
                pos = length
            take = text[left:pos]
            if left>0:
                if take=='' and not quot:
                    join = True
                elif quot:
                    if join:
                        arr[-1] = (arr[-1][0]+"'"+take,True)
                        join = False
                    else:
                        arr.append((take,True))
                else:
                    arr.append((take.strip(),False))
            quot = not quot
            left = pos+1
        if not quot:
            return (None,'Last quote not closed in '+orig)
        if len(arr)>0 and arr[-1][0]=='':
            arr.pop()
        
        actions = []
        combine = False
        for param, quot in arr:
            one = param
            if quot:
                if combine:
                    actions[-1]['V'] = param
                    combine = False
                else:
                    actions.append({
                      'A': None,
                      'V': param,
                      'O': one,
                    })
                continue
            elif combine:
                return (None,'Wrong concatenation "'+param+'" in '+orig)
            param = reg_clean.sub('',param)
            while param!='':
                m = reg_oper.match(param)
                if not m:
                    if param=='+' or param=='-':
                        actions.append({
                          'A': False,
                          'V': param=='+',
                          'O': one,
                        })
                        break
                    return (None,'Wrong expression "'+param+'" in '+orig)
                m_flag = m.group(1)=='='
                m_mul = m.group(2)
                m_val = m.group(3)
                m_shift = m.group(4)
                m_size = m.group(5)
                m_tok = -1
                if m_val is not None:
                    if m_mul==',':
                        if m_flag:
                            return (None,'Concatenation doesn\'t support \'=\' prefix: "'+param+'" in '+orig)
                        if (len(m_val)>0) and (m_val[0]=='0'):
                            if m_val=='0':
                                m_tok = 0
                            elif m_val=='00':
                                m_tok = -2
                            elif m_val=='000':
                                m_tok = -3
                            elif m_val=='0000':
                                m_tok = -4
                            else:
                                m_tok = None
                        elif m_val=='':
                            m_tok = -5
                            combine = True
                            m_val = None
                        else:
                            m_tok = to_int(m_val)
                            if (m_tok is not None) and not (m_tok>=0):
                                m_tok = None
                        if m_tok is None:
                            return (None,'Bad param for concatenation "'+param+'" in '+orig)
                    else:
                        m_val = to_float(m_val)
                        if m_val is None:
                            return (None,'Bad param for multiplication "'+param+'" in '+orig)
                        m_mul = m_mul=='*'
                    m_size = -1
                    m_shift = 0
                else:
                    m_size = int(m_size)
                    if m_shift=='+':
                        m_shift = m_size
                        m_size = -1
                    elif m_shift=='-':
                        m_shift = -m_size
                        m_size = -1
                    else:
                        m_shift = 0
                    m_val = 1
                    m_mul = None
                actions.append({
                  'A': True,
                  'V': m_val,
                  'W': m_mul,
                  'S': m_size,
                  'R': m_shift,
                  'F': m_flag,
                  'T': m_tok,
                  'O': one,
                })
                param = param[len(m.group(0)):]
        if combine:
            return (None,'Unfinished concatenation in '+orig)
        actions.append({
          'A': None,
          'V': None,
        })
        can_file = True
        can_add = False
        can_mul = False
        for act in actions:
            act['M'] = False
            A = act['A']
            if A==None:
                if act['V']==None:
                    if can_file:
                        return (None,'Need quoted string after last + or - in '+orig)
                    act['M'] = True
                    break
                if can_file:
                    can_add = True
                    can_mul = True
                    can_file = False
                else:
                    return (None,'Quoted string without preceding + or - at \''+act['O']+'\' in '+orig)
            elif A==True:
                if can_mul:
                    can_file = False
                    can_add = True
                    can_mul = True
                    if act['F']:
                        act['M'] = True
                else:
                    return (None,'Cannot multiply or modify at "'+act['O']+'" in '+orig)
            else:
                if can_add:
                    can_file = True
                    can_mul = False
                    can_add = False
                    act['M'] = True
                else:
                    return (None,'Cannot merge at "'+act['O']+'" in '+orig)
        left = None
        right = None
        add = 0
        for act in actions:
            if act['M'] and (left is not None):
                if add!=0:
                    if only_count:
                        if left>right:
                            right = left
                    else:
                        (vectors1,length1) = left.size()
                        (vectors2,length2) = right.size()
                        if length1!=length2:
                            return (None,'Cannot merge different embeddings in '+orig)
                        if vectors1!=vectors2:
                            if vectors1<vectors2:
                                target = torch.zeros(vectors2,length1).to(device=devices.device,dtype=torch.float32)
                                target[0:vectors1] = left
                                left = target
                            else:
                                target = torch.zeros(vectors1,length2).to(device=devices.device,dtype=torch.float32)
                                target[0:vectors2] = right
                                right = target
                        if add>0:
                            right = left+right
                        else:
                            right = left-right
                left = None
            A = act['A']
            if A==None:
                line = act['V']
                if line==None:
                    return (right,None)
                right = grab_vectors(line)
                if right==None:
                    return (None,'Failed to parse \''+line+'\' in '+orig)
                if only_count:
                    right = right.size(0)
            elif A==False:
                if act['V']:
                    add = 1
                else:
                    add = -1
                left = right
                right = None
            else:
                s = act['S']
                r = act['R']
                t = act['T']
                if only_count:
                    if t!=-1:
                        right += 1
                    elif (r==0)and(s>=0):
                        right = s
                else:
                    if t!=-1:
                        if t<0:
                            if t==-2:
                                t = shared.sd_model.cond_stage_model.id_pad
                            elif t==-3:
                                t = shared.sd_model.cond_stage_model.id_end
                            elif t==-4:
                                t = shared.sd_model.cond_stage_model.id_start
                            else:
                                res = grab_vectors(act['V'])
                                t = None
                                if res is None:
                                    return (None,'Failed to parse \''+act['V']+'\' in '+orig)
                        if t is not None:
                            if not check_vocab(t):
                                return (None,'Unknown token value \''+str(t)+'\' in '+orig)
                            res = tokens_to_vectors([t])
                        if res is None:
                            return (None,'Failed to convert token \''+str(t)+'\' in '+orig)
                        if right is None:
                            right = res
                        else:
                            right = torch.cat([right,res])
                    elif r!=0:
                        right = right.roll(r,dims=0)
                    else:
                        if s>=0:
                            (vectors,length) = right.size()
                            if vectors>s:
                                right = right[0:s]
                            elif vectors<s:
                                target = torch.zeros(s,length).to(device=devices.device,dtype=torch.float32)
                                target[0:vectors] = right
                                right = target
                        elif act['W']==True:
                            right = right*act['V']
                        elif  act['W']==False:
                            right = right/act['V']
        return (right,None)

    def grab_embedding_cache():
        db = modules.sd_hijack.model_hijack.embedding_db
        field = '__embedding_merge_cache_'
        if hasattr(db,field):
            cache = getattr(db,field)
        else:
            cache = {'_':0,'-':0,'/':0}
            setattr(db,field,cache)
        return cache
        
    def register_embedding(name,embedding):
        self = modules.sd_hijack.model_hijack.embedding_db
        model = shared.sd_model
        if hasattr(self,'register_embedding_by_name'):
            return self.register_embedding_by_name(embedding,model,name)
        # /modules/textual_inversion/textual_inversion.py
        try:
            ids = model.cond_stage_model.tokenize([name])[0]
            first_id = ids[0]
        except:
            return
        if embedding is None:
            if self.word_embeddings[name] is None:
                return
            del self.word_embeddings[name]
        else:
            self.word_embeddings[name] = embedding
        if first_id not in self.ids_lookup:
            if embedding is None:
                return
            self.ids_lookup[first_id] = []
        save = [(ids, embedding)] if embedding is not None else []
        old = [x for x in self.ids_lookup[first_id] if x[1].name!=name]
        self.ids_lookup[first_id] = sorted(old + save, key=lambda x: len(x[0]), reverse=True)
        return embedding

    def make_temp_embedding(name,vectors,cache,fake):
        if name in cache:
            embed = cache[name]
            if fake>0:
                return
        else:
            if fake>0:
                vectors = torch.zeros((fake,16))
            embed = Embedding(vectors,name)
            cache[name] = embed
        embed.vec = vectors
        embed.step = None
        shape = vectors.size()
        embed.vectors = shape[0]
        embed.shape = shape[-1]
        embed.cached_checksum = None
        embed.filename = ''
        register_embedding(name,embed)
    
    def reset_temp_embeddings(prod,unregister):
        cache = grab_embedding_cache()
        num = cache[prod]
        cache[prod] = 0
        for a,b in (('<','>'),('{','}')):
            i = num
            while i>0:
                tgt = a+"'EM"+prod+str(i)+"'"+b
                if tgt in cache:
                    embed = cache[tgt]
                    embed.vec = None
                    embed.shape = None
                    embed.vectors = 0
                    embed.cached_checksum = None
                    del cache[tgt]
                    if unregister:
                        register_embedding(tgt,None)
                i = i-1
        return cache

    def add_temp_embedding(vectors,cache,prod,curly,fake):
        if fake>0:
            prod = '/'
            num = (cache[prod] or 0)
            if fake>num:
                cache[prod] = fake
            num = fake
        else:
            prod = '_' if prod else '-'
            num = 1+(cache[prod] or 0)
            cache[prod] = num
        name = "'EM"+prod+str(num)+"'"
        if curly:
            name = '{'+name+'}'
        else:
            name = '<'+name+'>'
        make_temp_embedding(name,vectors,cache,fake)
        return name
    
    def parse_infotext(text):
        orig = text
        text += '\n'
        pos = re.search(r"\bEmbeddingMerge:\s*(\"?[<{])'EM_",text)
        if pos is None:
            return (None,orig)
        head = text[:pos.span(0)[0]].rstrip()
        if len(head)>0 and head[-1]==',':
            head = head[:-1]
        text = text[pos.span(1)[0]:]
        if len(text)<2:
            return (None,orig)
        what = text[0]
        if what=='"':
            unquoted = None
        else:
            if what=='<':
                unquoted = '>'
            elif what=='{':
                unquoted = '}'
            else:
                return (None,orig)
        if unquoted is not None:
            stop = min_or_all(text.find(unquoted+','),text.find(unquoted+'\n'),-1)
            if stop<0:
                return (None,orig)
            stop += 1
            tail = text[stop:]
            line = text[:stop]
        else:
            stop = (text+'\n').find('\n')
            part = text[:stop]
            left = 0
            while True:
                right = part.find('"',left+1)
                if right<0:
                    return (None,orig)
                try:
                    line = json.loads('['+part[:right+1].strip()+']')[0]
                    break
                except:
                    left = right
            tail = part[right+1:]+text[stop:]
        return (line,head+tail)

    def parse_mergeseq(seq):
        res = None
        seq = seq.lstrip()
        while True:
            left = seq[0:5]
            if left=="<'EM_":
                right = "'>="
            elif left=="{'EM_":
                right = "'}="
            else:
                return res
            stop = seq.find(right)
            if stop<1:
                return res
            what = seq[0:stop+2]
            seq = seq[stop+3:]
            left = seq[0:2]
            if left=="<'":
                right = '>, '
            elif left=="{'":
                right = '}, '
            else:
                return res
            stop = min_or_all(seq.find(right+"<'"),seq.find(right+"{'"),len(seq))
            repl = seq[0:stop+1]
            seq = seq[stop+3:]
            if res is None:
                res = {}
            res[what] = repl
    
    def min_or_all(a,b,n):
        if a>=0:
            if b>=0:
                if a<b:
                    return a
                return b
            else:
                return a
        elif b>=0:
            return b
        return n
        
    def dict_replace(di,text):
        for key in di:
            text = text.replace(key,di[key])
        return text

    gr_lock = threading.Lock()
    
    def gr_func(gr_name,gr_text,gr_radio,store):
        with gr_lock:
            gr_orig = gr_text
            font = 'font-family:Consolas,Courier New,Courier,monospace;'
            table = '<style>.webui_embedding_merge_table,.webui_embedding_merge_table td,.webui_embedding_merge_table th{border:1px solid gray;border-collapse:collapse}.webui_embedding_merge_table td,.webui_embedding_merge_table th{padding:2px 5px !important;text-align:center !important;vertical-align:middle;'+font+'font-weight:bold;}</style><table class="webui_embedding_merge_table">'
            (reparse,request) = parse_infotext(gr_text)
            if reparse is not None:
                reparse = parse_mergeseq(reparse)
                if reparse is None:
                    return ('<center><b>Prompt restore failed!</n></center>',gr_name,gr_orig)
                else:
                    request = dict_replace(reparse,request)
                    return ('<center><b>Prompt restored.</n></center>',gr_name,request)
            if gr_text[:1]=="'":
                clipskip = opts.CLIP_stop_at_last_layers
                opts.CLIP_stop_at_last_layers = 1
                (res,err) = merge_parser(gr_text,False)
                opts.CLIP_stop_at_last_layers = clipskip
                if (res is not None) and res.numel()==0:
                    err = 'Result is ZERO vectors!'
                if err is not None:
                    txt = '<b style="'+font+'">'+html.escape(err)+'</b>'
                else:
                    txt = table+'<tr><th>Index</th><th>Min</th><th>Max</th><th>Sum</th><th>Abs</th><th>Len</th><th>Std</th>'
                    i = 1
                    for one in res:
                        txt += '<tr><td>{}</td>{}</tr>'.format(i,tensor_info(one))
                        i += 1
                    txt += '<tr><td colspan="6">&nbsp;</td></tr>'
                    txt += '<tr><td>ALL:</td>{}</tr>'.format(tensor_info(res))
                    txt += '</table>'
                return ('<center>'+txt+'</center>',need_save_embed(store,gr_name,res),gr_orig)
            if gr_text.find("<'")>=0 or gr_text.find("{'")>=0:
                cache = reset_temp_embeddings('-',False)
                used = {}
                (res,err) = merge_one_prompt(cache,None,{},used,gr_text,False,False)
                if err is not None:
                    txt = '<b style="'+font+'">Embedding Merge failed - '+html.escape(err)+'</b>'
                    return ('<center>'+txt+'</center>',gr_name,gr_orig)
                gr_text = res
            by_none = 0
            by_comma = 1
            by_parts = 2
            by_words = 3
            by_tokens = 4
            by_vectors = 5
            tok2txt = tokens_to_text()
            clipskip = opts.CLIP_stop_at_last_layers
            opts.CLIP_stop_at_last_layers = 1
            if gr_radio!=by_comma:
                res = text_to_vectors(gr_text)
                if (gr_radio==by_none) and (res is not None) and (len(res)!=0):
                    res = [res]
            else:
                res = []
                split = gr_text.split(',')
                for part in split:
                    one = text_to_vectors(part.strip())
                    if one:
                        res.append(one)
                    else:
                        res = None
                        break
            opts.CLIP_stop_at_last_layers = clipskip
            if (res is None) or (len(res)==0):
                if gr_text.strip()=='':
                    return ('',gr_name,gr_orig)
                txt = '<b>Failed to parse! (Possibly there are more than 75 tokens; or extra spaces inside embed names). Embeddings are not shown now:</b><br/><br/>'
                tokens = text_to_tokens(gr_text)
                if tokens:
                    txt += table+'<tr><th>Index</th><th>Vectors</th><th>Text</th><th>Token</th></tr>'
                    if tok2txt:
                        pairs = tok2txt(tokens)
                    else:
                        pairs = [([tok],'<ERROR>') for tok in tokens]
                    index = 1
                    for arr, text in pairs:
                        length = len(arr)
                        if length==0:
                            continue
                        txt += '<tr><td>'+(str(index) if length==1 else str(index)+'-'+str(index+length-1))+'</td><td>'+str(length)+'</td><td>'+html.escape('"'+text+'"')+'</td><td>'+(', '.join([str(a) for a in arr]))+'</td></tr>'
                        index += length
                    txt += '</table>'
                return ('<center>'+txt+'</center>',gr_name,gr_orig)
            txt = table+'<tr><th>Index</th><th>Vectors</th><th>Text</th><th>Token</th><th>Min</th><th>Max</th><th>Sum</th><th>Abs</th><th>Len</th><th>Std</th></tr>'
            index = 1
            join = False
            if gr_radio==by_words:
                join = True
                gr_radio = by_tokens
            elif (gr_radio==by_none) or (gr_radio==by_comma):
                r_res = []
                for one in res:
                    r_tensor = []
                    r_name = ''
                    r_tokens = []
                    for tensor, name, tokens in one:
                        r_tensor.append(tensor)
                        if tok2txt and tokens and gr_radio==by_none:
                            split = tok2txt(tokens)
                            name = ''
                            tokens = []
                            for s_tokens, s_name in split:
                                name += s_name
                                tokens += s_tokens
                        r_name += name
                        if tokens:
                            r_tokens += tokens
                        else:
                            r_tokens += ['*_'+str(tensor.size(0))]
                            if gr_radio==by_none:
                                r_name += ' '
                    r_res.append((torch.cat(r_tensor),r_name,r_tokens))
                res = r_res
                gr_radio = by_parts
            for tensor, name, tokens in res:
                split = None
                size = tensor.size(0)
                span = ''
                if gr_radio!=by_parts:
                    span = ' rowspan="'+str(size)+'"'
                    if tokens and tok2txt:
                        split = tok2txt(tokens)
                        if join:
                            comb = []
                            last = -1
                            for s_arr, s_text in split:
                                if (last<0) or (comb[last][1][-1:]==' '):
                                    comb.append((s_arr,s_text))
                                    last += 1
                                else:
                                    comb[last] = (comb[last][0]+s_arr,comb[last][1]+s_text)
                            split = comb
                    if gr_radio==by_tokens:
                        if split is not None:
                            span = ' rowspan="'+str(len(split))+'"'
                        else:
                            span = ''
                if gr_radio==by_vectors:
                    head = '<td'+span+'>'+str(size)+'</td>'
                else:
                    head = '<td'+span+'>'+(str(index) if size==1 else str(index)+'-'+str(index+size-1))+'</td><td'+span+'>'+str(size)+'</td>'
                if split is None:
                    head += '<td'+span+'>'+html.escape('"'+name+'"')+'</td>'
                if (gr_radio==by_vectors) or ((gr_radio==by_tokens) and (tokens is not None)):
                    i = 0
                    part = 0
                    j = 0
                    ten = None
                    column = ''
                    toks = None
                    for one in list(tensor):
                        index += 1
                        i += 1
                        use = one
                        if split is not None:
                            if part==0:
                                pair = split[j]
                                part = len(pair[0])
                                if gr_radio==by_tokens:
                                    column = '<td>'+html.escape('"'+pair[1]+'"')+'</td>'
                                    toks = ', '.join([str(t) for t in pair[0]])
                                else:
                                    column = '<td rowspan="'+str(part)+'">'+html.escape('"'+pair[1]+'"')+'</td>'
                                j += 1
                        part -= 1
                        if gr_radio==by_tokens:
                            if ten==None:
                                ten = []
                            ten.append(one)
                            if part>0:
                                continue
                            use = torch.stack(ten)
                            tok = toks if tokens else '*'
                        else:
                            tok = tokens[i-1] if tokens else '*_'+str(i)
                        txt += '<tr>{}{}<td>{}</td>{}</tr>'.format(('<td>'+str(index-1)+'</td>' if gr_radio==by_vectors else '')+head,column,tok,tensor_info(use))
                        column = ''
                        head = ''
                        ten = None
                else:
                    index += size   
                    txt += '<tr>{}<td>{}</td>{}</tr>'.format(head,', '.join([str(t) for t in tokens]) if tokens else '*',tensor_info(tensor))
            txt += '</table>'
            return ('<center>'+txt+'</center>',need_save_embed(store,gr_name,res),gr_orig)

    def tensor_info(tensor):
        return '<td>{:>-14.8f}</td><td>{:>+14.8f}</td><td>{:>+14.8f}</td><td>{:>14.8f}</td><td>{:>14.8f}</td><td>{:>14.8f}</td>'.format(tensor.min().item(),tensor.max().item(),tensor.sum().item(),tensor.abs().sum().item(),torch.linalg.norm(tensor,ord=2),tensor.std()).replace(' ','&nbsp;')

    merge_dir = None
    
    def need_save_embed(store,name,vectors):
        if not store:
            return name
        name = ''.join( x for x in name if (x.isalnum() or x in '._- ')).strip()
        if name=='':
            return name
        try:
            if type(vectors)==list:
                vectors = torch.cat([r[0] for r in vectors])
            file = modules.textual_inversion.textual_inversion.create_embedding('_EmbeddingMerge_temp',vectors.size(0),True,init_text='')
            pt = torch.load(file,map_location='cpu')
            token = list(pt['string_to_param'].keys())[0]
            pt['string_to_param'][token] = vectors.cpu()
            torch.save(pt,file)
            target = os.path.join(merge_dir,name+'.pt')
            os.replace(file,target)
            modules.sd_hijack.model_hijack.embedding_db.load_textual_inversion_embeddings()
            return ''
        except:
            traceback.print_exc()
            return name

    def embedding_merge_dir():
        try:
            nonlocal merge_dir
            merge_dir = os.path.join(cmd_opts.embeddings_dir,'embedding_merge')
            # don't actually need this, since it is a subfolder which will be read recursively:
            #modules.sd_hijack.model_hijack.embedding_db.add_embedding_dir(merge_dir)
            os.makedirs(merge_dir)
        except:
            pass

    def raise_sd_error(p,msg):
        class Exception_From_EmbeddingMergeExtension_():
            def __getattribute__(self,_):
                raise Exception_From_EmbeddingMergeExtension(msg)
        p.__class__ = Exception_From_EmbeddingMergeExtension_

    def merge_one_prompt(cache,texts,parts,used,prompt,prod,only_count):
        try:
            cnt = 0
            if (prompt is None) or (prompt==''):
                return (prompt,None)
            if texts is not None:
                if prompt in texts:
                    return (texts[prompt],None)
            orig = prompt
            left = 0
            while True:
                curly = prompt.find("{'",left)
                left = prompt.find("<'",left)
                if (curly>=0 and curly<left) or (left<0):
                    left = curly
                    curly = True
                else:
                    curly = False
                if left<0:
                    if texts is not None:
                        texts[orig] = prompt
                    return (prompt,None)
                right = left
                while True:
                    right = prompt.find('}' if curly else '>',right+1)
                    if right<0:
                        if curly:
                            return (None,'Not found closing "}" after "{\'"')
                        else:
                            return (None,'Not found closing ">" after "<\'"')
                    if (prompt.count("'",left,right)&1)==0:
                        break
                part = prompt[left+1:right].strip()
                if part in parts:
                    embed = parts[part]
                else:
                    (res,err) = merge_parser(part,only_count)
                    if err is not None:
                        return (None,err)
                    if only_count:
                        if (res is None) or (res==0):
                            embed = ''
                        else:
                            embed = add_temp_embedding(None,cache,prod,curly,res)
                    else:
                        if (res is None) or (res.numel()==0):
                            embed = ''
                        else:
                            embed = add_temp_embedding(res,cache,prod,curly,0)
                    if used is not None:
                        used[embed] = part
                    parts[part] = embed
                prefix = prompt[:left].rstrip()+' '+embed
                left = len(prefix)
                prompt = prefix+' '+(prompt[right+1:].lstrip())
        except:
            traceback.print_exc()
            return (None,'Fatal error?')

    def embedding_merge_extension(p):
        cache = reset_temp_embeddings('_',False)
        texts = {}
        parts = {}
        used = {}
        pair = [[
            p.all_prompts,
            p.prompt if type(p.prompt)==list else [p.prompt],
        ],[
            p.all_negative_prompts,
            p.negative_prompt if type(p.negative_prompt)==list else [p.negative_prompt],
        ]]
        for arr in pair:
            ok = False
            fail = None
            for one in arr:
                if one is not None:
                    for i in range(len(one)):
                        (res,err) = merge_one_prompt(cache,texts,parts,used,one[i],True,False)
                        if err is not None:
                            if fail is None:
                                fail = err
                        else:
                            one[i] = res
                            ok = True
            if not ok and fail is not None:
                raise_sd_error(p,'\n\nEmbedding Merge failed - '+err+'\n')
                return
        arr = pair[0]+pair[1]
        p.all_prompts = arr[0]
        p.all_negative_prompts = arr[2]
        p.prompt = arr[1] if type(p.prompt)==list else arr[1][0]
        p.negative_prompt = arr[3] if type(p.negative_prompt)==list else arr[3][0]
        gen = ''
        for embed in used:
            if embed[0]=='<':
                gen += embed+'=<'+used[embed]+'>, '
            else:
                gen += embed+'={'+used[embed]+'}, '
        if gen!='':
            p.extra_generation_params['EmbeddingMerge'] = gen[:-2]

    try:
        cls = modules.sd_hijack.StableDiffusionModelHijack
        get_prompt_lengths = cls.get_prompt_lengths
        field = '__embedding_merge_wrapper'
        def hook_prompt_lengths(self,text):
            if text.find("<'")<0 and text.find("{'")<0:
                return get_prompt_lengths(self,text)
            (res,err) = merge_one_prompt(grab_embedding_cache(),None,{},None,text,True,True)
            if err is not None:
                return -1,-1
            return get_prompt_lengths(self,res)
        if hasattr(get_prompt_lengths,field):
            get_prompt_lengths = getattr(get_prompt_lengths,field)
        setattr(hook_prompt_lengths,field,get_prompt_lengths)
        cls.get_prompt_lengths = hook_prompt_lengths
    except:
        traceback.print_exc()

    def on_infotext_pasted(infotext,result):
        if 'EmbeddingMerge' in result:
            reparse = result['EmbeddingMerge']
            if reparse[:1]=='"':
                try:
                    reparse = json.loads('['+reparse.strip()+']')[0]
                    reparse = parse_mergeseq(reparse)
                except:
                    reparse = None
            else:
                reparse = parse_mergeseq(reparse)
            request = None
        else:
            (reparse,request) = parse_infotext(infotext)
            if reparse is not None:
                reparse = parse_mergeseq(reparse)
        if reparse is not None:
            if 'Prompt' in result:
                if (request is not None) and (result['Prompt']==infotext):
                    result['Prompt'] = request
                result['Prompt'] = dict_replace(reparse,result['Prompt'])
            if 'Negative prompt' in result:
                result['Negative prompt'] = dict_replace(reparse,result['Negative prompt'])
    setattr(_webui_embedding_merge_,'on_infotext_pasted',on_infotext_pasted)
    
    def on_script_unloaded():
        reset_temp_embeddings('_',True)
        reset_temp_embeddings('-',True)
        reset_temp_embeddings('/',True)
        try:
            cls = modules.sd_hijack.StableDiffusionModelHijack
            get_prompt_lengths = cls.get_prompt_lengths
            field = '__embedding_merge_wrapper'
            if hasattr(get_prompt_lengths,field):
                cls.get_prompt_lengths = getattr(get_prompt_lengths,field)
        except:
            traceback.print_exc()
        try:
            db = modules.sd_hijack.model_hijack.embedding_db
            field = '__embedding_merge_cache_'
            if hasattr(db,field):
                delattr(db,field)
        except:
            traceback.print_exc()
    setattr(_webui_embedding_merge_,'on_script_unloaded',on_script_unloaded)
    setattr(_webui_embedding_merge_,'embedding_merge_extension',embedding_merge_extension)
    embedding_merge_dir()
    return gr_tab

class EmbeddingMergeExtension(scripts.Script):
    def title(self):
        return 'Embedding Merge'
    def show(self,is_img2img):
        return scripts.AlwaysVisible
    def process(self,p):
        if hasattr(_webui_embedding_merge_,'embedding_merge_extension'):
            getattr(_webui_embedding_merge_,'embedding_merge_extension')(p)



script_callbacks.on_ui_tabs(_webui_embedding_merge_())
script_callbacks.on_infotext_pasted(_webui_embedding_merge_.on_infotext_pasted)
script_callbacks.on_script_unloaded(_webui_embedding_merge_.on_script_unloaded)

#EOF
