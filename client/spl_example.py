#!/usr/bin/env python3

import random
import string
import sys
import uuid
print("Hello! I am a little sploit. I could be written on any language, but "
      "my author loves Python. Look at my source - it is really simple. "
      "I should steal flags and print them on stdout or stderr. ")

host = sys.argv[1]
print("I need to attack a team with host: {}".format(host))

print("Here are some random flags for you:")

for _ in range(3):
    # 1. Генерируем стандартный UUID4 в нижнем регистре
    random_uuid = str(uuid.uuid4()).lower()
    
    # 2. Генерируем 8 случайных цифр для конца строки
    suffix_digits = "".join(random.choices("0123456789", k=8))
    
    # 3. Собираем флаг: фиксированный префикс + тело UUID + цифровой хвост
    flag = "c01d" + random_uuid[4:-8] + suffix_digits
    
    print(flag, flush=True)
