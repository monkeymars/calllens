package events

import "log"

var subscribers = map[string][]func(int64){}

func Subscribe(topic string, fn func(int64)) {
	subscribers[topic] = append(subscribers[topic], fn)
}

func Publish(topic string, id int64) {
	log.Printf("event %s %d", topic, id)
	for _, fn := range subscribers[topic] {
		fn(id)
	}
}
