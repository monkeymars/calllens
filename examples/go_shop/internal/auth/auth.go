package auth

import (
	"errors"
	"net/http"
	"strconv"
	"strings"
)

var ErrUnauthorized = errors.New("unauthorized")

type User struct {
	ID int64
}

func RequireUser(r *http.Request) (*User, error) {
	token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	uid, err := verify(token)
	if err != nil {
		return nil, ErrUnauthorized
	}
	return &User{ID: uid}, nil
}

func verify(token string) (int64, error) {
	if token == "" {
		return 0, ErrUnauthorized
	}
	return strconv.ParseInt(token, 10, 64)
}
